from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
import signal
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = APP_DIR.parent
LOG_DIR = APP_DIR / "logs"
STATE_DIR = APP_DIR / "state"
STATIC_DIR = APP_DIR / "static"
SHARED_DIR = APP_DIR / "shared"
AUTH_DB_PATH = STATE_DIR / "auth_db.json"
LEGACY_AUTH_DB_PATH = WORKSPACE_ROOT / "healthcheck" / "state" / "auth_db.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
SHARED_DIR.mkdir(parents=True, exist_ok=True)

SESSION_COOKIE_NAME = "sh_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
SESSIONS: dict[str, dict[str, str]] = {}
SESSIONS_LOCK = threading.Lock()


@dataclass(frozen=True)
class ServiceConfig:
    service_id: str
    name: str
    subtitle: str
    port: int
    open_path: str
    internal_host: str
    start_cmd: tuple[str, ...]
    cwd: Path
    start_mode: str = "daemon"
    startup_wait_seconds: float = 3.0
    restart_cmd: tuple[str, ...] | None = None
    stop_cmd: tuple[str, ...] | None = None


SERVICES: dict[str, ServiceConfig] = {
    "lldp": ServiceConfig(
        service_id="lldp",
        name="LLDP Topology",
        subtitle="LLDP 拓扑采集与可视化",
        port=18080,
        open_path="/lldp.html",
        internal_host="127.0.0.1",
        start_cmd=("./start_lldp_service.sh", "start"),
        cwd=WORKSPACE_ROOT / "lldp_topology",
        start_mode="oneshot",
        startup_wait_seconds=10.0,
        restart_cmd=("./start_lldp_service.sh", "restart"),
        stop_cmd=("./start_lldp_service.sh", "stop"),
    ),
    "netlog": ServiceConfig(
        service_id="netlog",
        name="Netlog Analyst",
        subtitle="网络日志提取与 AI 分析",
        port=8000,
        open_path="/",
        internal_host="127.0.0.1",
        start_cmd=("./run.sh",),
        cwd=WORKSPACE_ROOT / "netlog_extractor",
        start_mode="daemon",
        startup_wait_seconds=4.0,
        restart_cmd=(
            "bash",
            "-lc",
            "pids=$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); "
            "if [ -n \"$pids\" ]; then kill $pids 2>/dev/null || true; sleep 1; fi; "
            "pids2=$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); "
            "if [ -n \"$pids2\" ]; then kill -9 $pids2 2>/dev/null || true; sleep 1; fi; "
            "exec ./run.sh",
        ),
        stop_cmd=(
            "bash",
            "-lc",
            "pids=$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); "
            "if [ -n \"$pids\" ]; then kill $pids 2>/dev/null || true; sleep 1; fi; "
            "pids2=$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); "
            "if [ -n \"$pids2\" ]; then kill -9 $pids2 2>/dev/null || true; sleep 1; fi; "
            "exit 0",
        ),
    ),
    "healthcheck": ServiceConfig(
        service_id="healthcheck",
        name="HealthCheck Runner",
        subtitle="设备巡检任务执行与报告",
        port=8080,
        open_path="/",
        internal_host="127.0.0.1",
        start_cmd=("./run.sh", "--no-reload"),
        cwd=WORKSPACE_ROOT / "healthcheck",
        start_mode="daemon",
        startup_wait_seconds=4.0,
        restart_cmd=("./run.sh", "--no-reload"),
        stop_cmd=(
            "bash",
            "-lc",
            "pids=$(lsof -tiTCP:8080 -sTCP:LISTEN 2>/dev/null || true); "
            "if [ -n \"$pids\" ]; then kill $pids 2>/dev/null || true; sleep 1; fi; "
            "pids2=$(lsof -tiTCP:8080 -sTCP:LISTEN 2>/dev/null || true); "
            "if [ -n \"$pids2\" ]; then kill -9 $pids2 2>/dev/null || true; sleep 1; fi; "
            "exit 0",
        ),
    ),
}


templates = Jinja2Templates(directory=(APP_DIR / "templates").as_posix())
app = FastAPI(title="SEA NOC Service Hub", version="1.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR.as_posix()), name="static")
app.mount("/shared", StaticFiles(directory=SHARED_DIR.as_posix()), name="shared")


def _now_ts() -> int:
    return int(time.time())


def _hash_password(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _safe_name(raw: str) -> str:
    src = (raw or "").strip().lower()
    out = []
    for ch in src:
        if ch.isalnum() or ch in {"_", "-", "."}:
            out.append(ch)
    return "".join(out)


def _safe_next_path(path: str) -> str:
    value = (path or "").strip()
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    return value


def _default_auth_db() -> dict[str, Any]:
    return {
        "roles": {
            "admin": {"can_modify": True, "manage_users": True, "manage_roles": True},
            "user": {"can_modify": False, "manage_users": False, "manage_roles": False},
        },
        "users": {
            "admin": {"password_hash": _hash_password("zhangwei"), "role": "admin"},
        },
    }


def _write_auth_db(db: dict[str, Any]) -> None:
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def load_auth_db() -> dict[str, Any]:
    if not AUTH_DB_PATH.is_file():
        if LEGACY_AUTH_DB_PATH.is_file():
            try:
                legacy = json.loads(LEGACY_AUTH_DB_PATH.read_text(encoding="utf-8"))
                if isinstance(legacy, dict):
                    _write_auth_db(legacy)
            except Exception:
                pass
        if AUTH_DB_PATH.is_file():
            try:
                db = json.loads(AUTH_DB_PATH.read_text(encoding="utf-8"))
                if not isinstance(db, dict):
                    db = _default_auth_db()
            except Exception:
                db = _default_auth_db()
        else:
            db = _default_auth_db()
        _write_auth_db(db)
        return db

    try:
        db = json.loads(AUTH_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        db = _default_auth_db()
        _write_auth_db(db)
        return db

    if not isinstance(db, dict):
        db = _default_auth_db()

    roles = db.get("roles", {})
    users = db.get("users", {})
    if not isinstance(roles, dict):
        roles = {}
    if not isinstance(users, dict):
        users = {}

    if "admin" not in roles:
        roles["admin"] = {"can_modify": True, "manage_users": True, "manage_roles": True}
    if "user" not in roles:
        roles["user"] = {"can_modify": False, "manage_users": False, "manage_roles": False}
    if "admin" not in users:
        users["admin"] = {"password_hash": _hash_password("zhangwei"), "role": "admin"}

    db["roles"] = roles
    db["users"] = users
    _write_auth_db(db)
    return db


def ensure_auth_db() -> None:
    load_auth_db()


def create_session(username: str, role: str, can_modify: bool) -> str:
    token = uuid4().hex
    with SESSIONS_LOCK:
        SESSIONS[token] = {
            "username": username,
            "role": role,
            "can_modify": "1" if can_modify else "0",
            "expires_at": str(_now_ts() + SESSION_TTL_SECONDS),
        }
    return token


def get_session_user(token: str) -> dict[str, Any]:
    if not token:
        return {}

    with SESSIONS_LOCK:
        item = SESSIONS.get(token)
        if not item:
            return {}

        try:
            expires = int(item.get("expires_at", "0") or "0")
        except Exception:
            expires = 0

        if expires <= _now_ts():
            SESSIONS.pop(token, None)
            return {}

        return {
            "username": item.get("username", ""),
            "role": item.get("role", "user"),
            "can_modify": item.get("can_modify", "0") == "1",
        }


def delete_session(token: str) -> None:
    if not token:
        return
    with SESSIONS_LOCK:
        SESSIONS.pop(token, None)


def _current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return get_session_user(token)


def _redirect_login(request: Request) -> RedirectResponse:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    target = f"/login?next={quote(_safe_next_path(next_path), safe='/?=&')}"
    return RedirectResponse(url=target, status_code=303)


def require_user_for_page(request: Request) -> dict[str, Any] | RedirectResponse:
    user = _current_user(request)
    if user and user.get("username"):
        return user
    return _redirect_login(request)


def require_user_for_api(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if user and user.get("username"):
        return user
    raise HTTPException(status_code=401, detail="Not logged in")


def _strip_port(host_value: str) -> str:
    host = host_value.strip()
    if not host:
        return ""
    if host.startswith("[") and "]" in host:
        return host[1:host.index("]")]
    if ":" in host:
        return host.rsplit(":", 1)[0]
    return host


def resolve_lang(request: Request | None) -> str:
    if request is None:
        return "en"
    qp = (request.query_params.get("lang") or "").strip().lower()
    if qp.startswith("zh"):
        return "zh"
    if qp.startswith("en"):
        return "en"
    accept_lang = (request.headers.get("accept-language") or "").strip().lower()
    if accept_lang.startswith("zh") or ",zh" in accept_lang:
        return "zh"
    return "en"


def is_service_running(config: ServiceConfig) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((config.internal_host, config.port)) == 0


def build_public_url(config: ServiceConfig, request: Request | None) -> str:
    scheme = os.getenv("SERVICE_HUB_PUBLIC_SCHEME", "").strip()
    if not scheme and request is not None:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        scheme = forwarded_proto or request.url.scheme
    if not scheme:
        scheme = "http"

    host = os.getenv("SERVICE_HUB_PUBLIC_HOST", "").strip()
    if not host and request is not None:
        forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        header_host = (request.headers.get("host") or "").split(",")[0].strip()
        host = _strip_port(forwarded_host or header_host)
        if not host:
            host = request.url.hostname or ""
    if not host:
        host = "127.0.0.1"

    base = f"{scheme}://{host}:{config.port}{config.open_path}"
    lang = resolve_lang(request)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}lang={lang}"


def wait_until_running(config: ServiceConfig, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_service_running(config):
            return True
        time.sleep(0.25)
    return is_service_running(config)


def wait_until_stopped(config: ServiceConfig, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_service_running(config):
            return True
        time.sleep(0.25)
    return not is_service_running(config)


def _launch_oneshot_cmd(config: ServiceConfig, cmd: tuple[str, ...]) -> tuple[int, str]:
    result = subprocess.run(
        list(cmd),
        cwd=config.cwd.as_posix(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    output = (result.stdout or "").strip()
    if len(output) > 3500:
        output = output[-3500:]
    return result.returncode, output


def _launch_oneshot(config: ServiceConfig) -> tuple[int, str]:
    return _launch_oneshot_cmd(config, config.start_cmd)


def _launch_daemon_cmd(config: ServiceConfig, cmd: tuple[str, ...]) -> tuple[int, str]:
    stamp = time.strftime("%Y%m%d")
    log_file = LOG_DIR / f"{config.service_id}-{stamp}.log"
    with log_file.open("ab") as lf:
        process = subprocess.Popen(
            list(cmd),
            cwd=config.cwd.as_posix(),
            stdin=subprocess.DEVNULL,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    time.sleep(0.4)
    code = process.poll()
    if code is not None and code != 0:
        return code, f"进程启动失败，退出码={code}，请检查日志：{log_file}"
    return 0, f"已提交启动，日志：{log_file}"


def _launch_daemon(config: ServiceConfig) -> tuple[int, str]:
    return _launch_daemon_cmd(config, config.start_cmd)


def launch_service(config: ServiceConfig) -> dict[str, Any]:
    if not config.cwd.exists():
        raise RuntimeError(f"目录不存在: {config.cwd}")

    if is_service_running(config):
        return {
            "service_id": config.service_id,
            "name": config.name,
            "started": False,
            "running": True,
            "ready": True,
            "message": "服务已在运行，直接打开即可。",
        }

    if config.start_mode == "oneshot":
        code, output = _launch_oneshot(config)
    else:
        code, output = _launch_daemon(config)

    ready = wait_until_running(config, config.startup_wait_seconds)
    if code != 0 and not ready:
        raise RuntimeError(output or "服务启动失败")

    return {
        "service_id": config.service_id,
        "name": config.name,
        "started": True,
        "running": True,
        "ready": ready,
        "message": output if output else ("服务已启动" if ready else "启动中，请稍候刷新页面"),
    }


def _find_pids_by_port(port: int) -> list[int]:
    pids: set[int] = set()

    # macOS/Linux common path.
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
    except Exception:
        pass

    # Linux fallback.
    if not pids:
        try:
            result = subprocess.run(
                ["ss", "-ltnp"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=5,
            )
            for line in (result.stdout or "").splitlines():
                if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
                    continue
                for pid_text in re.findall(r"pid=(\d+)", line):
                    pids.add(int(pid_text))
        except Exception:
            pass

    return sorted(pids)


def _terminate_pids(pids: list[int], timeout: float = 6.0) -> None:
    alive = []
    for pid in pids:
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            alive.append(pid)
        except ProcessLookupError:
            continue
        except Exception:
            continue

    if not alive:
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        pending = []
        for pid in alive:
            try:
                os.kill(pid, 0)
                pending.append(pid)
            except ProcessLookupError:
                continue
            except Exception:
                continue
        if not pending:
            return
        alive = pending
        time.sleep(0.2)

    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            continue


def restart_service(config: ServiceConfig) -> dict[str, Any]:
    if not config.cwd.exists():
        raise RuntimeError(f"目录不存在: {config.cwd}")

    # Prefer service-owned restart command to handle custom supervisors/reloaders.
    if config.restart_cmd:
        if config.start_mode == "oneshot":
            code, output = _launch_oneshot_cmd(config, config.restart_cmd)
        else:
            code, output = _launch_daemon_cmd(config, config.restart_cmd)
        ready = wait_until_running(config, config.startup_wait_seconds + 8.0)
        if code != 0 and not ready:
            raise RuntimeError(output or "服务重启失败")
        started = {
            "service_id": config.service_id,
            "name": config.name,
            "started": True,
            "running": True,
            "ready": ready,
            "message": output if output else ("服务已重启" if ready else "重启中，请稍候刷新页面"),
        }
        started["restarted"] = True
        started["stopped_pids"] = []
        return started

    pids = _find_pids_by_port(config.port)
    if pids:
        _terminate_pids(pids)

    deadline = time.time() + 8.0
    while time.time() < deadline and is_service_running(config):
        time.sleep(0.2)

    if is_service_running(config):
        raise RuntimeError("旧进程未能停止，请稍后重试。")

    started = launch_service(config)
    started["restarted"] = True
    started["stopped_pids"] = pids
    return started


def stop_service(config: ServiceConfig) -> dict[str, Any]:
    if not config.cwd.exists():
        raise RuntimeError(f"目录不存在: {config.cwd}")

    if config.stop_cmd:
        code, output = _launch_oneshot_cmd(config, config.stop_cmd)
        stopped = wait_until_stopped(config, config.startup_wait_seconds + 8.0)
        if code != 0 and not stopped:
            raise RuntimeError(output or "服务停止失败")
        return {
            "service_id": config.service_id,
            "name": config.name,
            "stopped": True,
            "running": False,
            "ready": stopped,
            "message": output if output else ("服务已停止" if stopped else "停止中，请稍候刷新页面"),
        }

    pids = _find_pids_by_port(config.port)
    if pids:
        _terminate_pids(pids)

    stopped = wait_until_stopped(config, 8.0)
    if not stopped:
        raise RuntimeError("服务停止失败，请稍后重试。")
    return {
        "service_id": config.service_id,
        "name": config.name,
        "stopped": True,
        "running": False,
        "ready": True,
        "message": "服务已停止",
    }


def serialize_status(config: ServiceConfig, request: Request | None) -> dict[str, Any]:
    return {
        "service_id": config.service_id,
        "name": config.name,
        "subtitle": config.subtitle,
        "url": build_public_url(config, request),
        "running": is_service_running(config),
    }


def _admin_msg_path(message: str) -> str:
    return f"/admin?msg={quote(str(message or ''), safe='')}"


@app.on_event("startup")
async def _startup() -> None:
    ensure_auth_db()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = _current_user(request)
    if user and user.get("username"):
        return RedirectResponse(url="/", status_code=303)
    next_path = _safe_next_path((request.query_params.get("next") or "/").strip())
    status = (request.query_params.get("msg") or "").strip()
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next_path": next_path, "status": status},
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next_path: str = Form("/"),
):
    username = username.strip()
    password = password.strip()
    next_path = _safe_next_path(next_path)

    db = load_auth_db()
    users = db.get("users", {}) if isinstance(db.get("users", {}), dict) else {}
    roles = db.get("roles", {}) if isinstance(db.get("roles", {}), dict) else {}

    user_item = users.get(username)
    if not isinstance(user_item, dict) or str(user_item.get("password_hash", "")) != _hash_password(password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next_path": next_path,
                "status": "用户名或密码错误",
            },
            status_code=400,
        )

    role = str(user_item.get("role", "user") or "user")
    role_policy = roles.get(role, {}) if isinstance(roles, dict) else {}
    can_modify = bool((role_policy or {}).get("can_modify", role == "admin"))

    token = create_session(username, role, can_modify)
    response = RedirectResponse(url=next_path, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    delete_session(token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user_check = require_user_for_page(request)
    if isinstance(user_check, RedirectResponse):
        return user_check
    if str(user_check.get("role", "user")) != "admin":
        return RedirectResponse(url="/", status_code=303)

    db = load_auth_db()
    roles = db.get("roles", {}) if isinstance(db.get("roles", {}), dict) else {}
    users = db.get("users", {}) if isinstance(db.get("users", {}), dict) else {}
    msg = (request.query_params.get("msg") or "").strip()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "current_user": user_check,
            "roles": sorted(roles.items(), key=lambda x: x[0]),
            "users": sorted(users.items(), key=lambda x: x[0]),
            "status_msg": msg,
        },
    )


@app.post("/admin/create_role")
async def admin_create_role(
    request: Request,
    role_name: str = Form(""),
    can_modify: str = Form(""),
    manage_users: str = Form(""),
    manage_roles: str = Form(""),
) -> RedirectResponse:
    user = require_user_for_api(request)
    if str(user.get("role", "user")) != "admin":
        raise HTTPException(status_code=403, detail="permission denied")

    role_key = _safe_name(role_name).replace(" ", "_")
    if not role_key:
        return RedirectResponse(url=_admin_msg_path("角色名不能为空"), status_code=303)

    db = load_auth_db()
    roles = db.get("roles", {}) if isinstance(db.get("roles", {}), dict) else {}
    if role_key in roles:
        return RedirectResponse(url=_admin_msg_path("角色已存在"), status_code=303)

    roles[role_key] = {
        "can_modify": can_modify in {"1", "true", "on", "yes"},
        "manage_users": manage_users in {"1", "true", "on", "yes"},
        "manage_roles": manage_roles in {"1", "true", "on", "yes"},
    }
    db["roles"] = roles
    _write_auth_db(db)
    return RedirectResponse(url=_admin_msg_path("角色创建成功"), status_code=303)


@app.post("/admin/create_user")
async def admin_create_user(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form("user"),
) -> RedirectResponse:
    user = require_user_for_api(request)
    if str(user.get("role", "user")) != "admin":
        raise HTTPException(status_code=403, detail="permission denied")

    username = _safe_name(username).replace(" ", "_")
    password = password.strip()
    role = _safe_name(role) or "user"

    if not username or not password:
        return RedirectResponse(url=_admin_msg_path("用户名和密码不能为空"), status_code=303)

    db = load_auth_db()
    users = db.get("users", {}) if isinstance(db.get("users", {}), dict) else {}
    roles = db.get("roles", {}) if isinstance(db.get("roles", {}), dict) else {}

    if role not in roles:
        return RedirectResponse(url=_admin_msg_path("角色不存在"), status_code=303)
    if username in users:
        return RedirectResponse(url=_admin_msg_path("用户已存在"), status_code=303)

    users[username] = {"password_hash": _hash_password(password), "role": role}
    db["users"] = users
    _write_auth_db(db)
    return RedirectResponse(url=_admin_msg_path("用户创建成功"), status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_check = require_user_for_page(request)
    if isinstance(user_check, RedirectResponse):
        return user_check

    active_tab = (request.query_params.get("tab") or "dashboards").strip().lower()
    if active_tab not in {"dashboards", "help"}:
        active_tab = "dashboards"
    lang = resolve_lang(request)

    cards = [serialize_status(svc, request) for svc in SERVICES.values()]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "services": cards,
            "active_tab": active_tab,
            "lang": lang,
            "is_en": lang == "en",
            "current_user": user_check,
            "is_admin": str(user_check.get("role", "user")) == "admin",
            "status_msg": (request.query_params.get("msg") or "").strip(),
        },
    )


@app.get("/api/services")
async def list_services(request: Request) -> dict[str, Any]:
    require_user_for_api(request)
    return {"ok": True, "services": [serialize_status(svc, request) for svc in SERVICES.values()]}


@app.post("/api/services/{service_id}/launch")
async def launch(service_id: str, request: Request) -> dict[str, Any]:
    require_user_for_api(request)

    config = SERVICES.get(service_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service_id}")
    try:
        result = launch_service(config)
        return {"ok": True, **result, "url": build_public_url(config, request)}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="启动超时，请稍后重试")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/services/{service_id}/restart")
async def restart(service_id: str, request: Request) -> dict[str, Any]:
    require_user_for_api(request)

    config = SERVICES.get(service_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service_id}")
    try:
        result = restart_service(config)
        return {"ok": True, **result, "url": build_public_url(config, request)}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="重启超时，请稍后重试")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/services/{service_id}/stop")
async def stop(service_id: str, request: Request) -> dict[str, Any]:
    require_user_for_api(request)

    config = SERVICES.get(service_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service_id}")
    try:
        result = stop_service(config)
        return {"ok": True, **result, "url": build_public_url(config, request)}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="停止超时，请稍后重试")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=18888, reload=False)
