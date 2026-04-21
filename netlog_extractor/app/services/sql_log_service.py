from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql


BASE_DIR = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = BASE_DIR.parent
LOG_TABLE_NAME = "logmessage1"
IPADDRESS_TABLE_NAME = "ipaddresslist"
TIME_COLUMN_CANDIDATES = [
    "create_time",
    "event_time",
    "log_time",
    "clock",
    "occur_time",
    "occurtime",
    "receive_time",
    "recv_time",
    "timestamp",
    "time",
    "datetime",
    "created_at",
    "event_ts",
]
MESSAGE_COLUMN_CANDIDATES = [
    "message",
    "log_message",
    "logmessage",
    "content",
    "body",
    "msg",
    "raw_message",
    "detail",
]
IP_COLUMN_CANDIDATES = [
    "device_ip",
    "host_ip",
    "hostip",
    "ipaddr",
    "ip_address",
    "source_ip",
    "src_ip",
    "ip",
    "management_ip",
    "mgmt_ip",
]
NAME_COLUMN_CANDIDATES = [
    "device_name",
    "devicename",
    "host_name",
    "hostname",
    "localhostname",
    "host",
    "node_name",
    "sysname",
]
VENDOR_COLUMN_CANDIDATES = [
    "vendor",
    "vendor_name",
    "platform",
    "brand",
    "manufacturer",
    "manufacture",
    "device_vendor",
    "dev_vendor",
]
RECENT_TIME_COLUMN_CANDIDATES = [
    "update_time",
    "updated_at",
    "create_time",
    "created_at",
    "discover_time",
    "last_seen",
    "modify_time",
    "gmt_modified",
    "clock",
    "time",
    "timestamp",
]


def load_dotenv_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


def ensure_env_loaded() -> None:
    explicit = get_env("DB_ENV_FILE", "")
    candidates = [
        explicit,
        str(BASE_DIR / ".env.mysql"),
        str(WORKSPACE_DIR / ".env.mysql"),
    ]
    for candidate in candidates:
        if candidate:
            load_dotenv_file(candidate)


def current_db_config_defaults() -> dict[str, Any]:
    ensure_env_loaded()
    return {
        "db_host": get_env("DB_HOST", "10.73.255.35"),
        "db_port": int(get_env("DB_PORT", "8080") or "8080"),
        "db_user": get_env("DB_USER", "readonly"),
        "db_password": os.getenv("DB_PASSWORD", ""),
        "db_name": get_env("DB_NAME", "monitoring"),
    }


def resolved_db_config(
    *,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_name: str | None = None,
) -> dict[str, Any]:
    cfg = current_db_config_defaults()
    return {
        "db_host": str(db_host or cfg["db_host"]).strip(),
        "db_port": int(db_port or cfg["db_port"]),
        "db_user": str(db_user or cfg["db_user"]).strip(),
        "db_password": str(db_password if db_password is not None else cfg["db_password"]),
        "db_name": str(db_name or cfg["db_name"]).strip(),
    }


def connect_db(
    *,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_name: str | None = None,
) -> pymysql.connections.Connection:
    cfg = resolved_db_config(
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_name=db_name,
    )
    host = cfg["db_host"]
    port = cfg["db_port"]
    user = cfg["db_user"]
    password = cfg["db_password"]
    database = cfg["db_name"]

    if not password:
        raise RuntimeError("Missing DB_PASSWORD. Set it in .env.mysql or environment")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8",
        connect_timeout=10,
        read_timeout=120,
        write_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def test_log_server_connection(
    *,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_name: str | None = None,
) -> dict[str, Any]:
    cfg = resolved_db_config(
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_name=db_name,
    )
    conn = connect_db(
        db_host=cfg["db_host"],
        db_port=cfg["db_port"],
        db_user=cfg["db_user"],
        db_password=cfg["db_password"],
        db_name=cfg["db_name"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db_name")
            db_row = cur.fetchone() or {}
            cur.execute(f"SHOW COLUMNS FROM `{LOG_TABLE_NAME}`")
            column_rows = cur.fetchall() or []
            columns = [str(row.get("Field") or "") for row in column_rows if row.get("Field")]
        return {
            "ok": True,
            "message": f"Connected to {cfg['db_host']}:{cfg['db_port']} / {db_row.get('db_name') or cfg['db_name']}",
            "db_host": cfg["db_host"],
            "db_port": cfg["db_port"],
            "db_user": cfg["db_user"],
            "db_name": db_row.get("db_name") or cfg["db_name"],
            "table": LOG_TABLE_NAME,
            "column_count": len(columns),
            "columns": columns,
        }
    finally:
        conn.close()


def _pick_column(columns: dict[str, str], candidates: list[str]) -> str | None:
    lowered = {key.lower(): key for key in columns}
    for candidate in candidates:
        actual = lowered.get(candidate.lower())
        if actual:
            return actual
    return None


def _is_integer_column(type_name: str) -> bool:
    low = str(type_name or "").lower()
    return any(token in low for token in ("int", "bigint", "smallint", "mediumint", "tinyint"))


def _coerce_row_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1_000_000_000_000:
            raw = raw / 1000.0
        try:
            return datetime.fromtimestamp(raw)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _normalize_device_names(device_name: str | None) -> list[str]:
    raw = str(device_name or "").strip()
    if not raw:
        return []
    values = {raw}
    short = raw.split(".", 1)[0].strip()
    if short:
        values.add(short)
    return sorted(v for v in values if v)


def _format_sql_time_value(dt: datetime, integer_column: bool) -> Any:
    if integer_column:
        return int(dt.timestamp())
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_vendor(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if "huawei" in low:
        return "huawei"
    if "arista" in low:
        return "arista"
    if "cisco" in low:
        return "cisco"
    return raw


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _lookup_ipaddresslist_device(cur: Any, device_ip: str) -> dict[str, Any]:
    if not device_ip:
        return {}
    try:
        cur.execute(f"SHOW COLUMNS FROM `{IPADDRESS_TABLE_NAME}`")
    except Exception:
        return {}

    column_rows = cur.fetchall() or []
    if not column_rows:
        return {}
    columns = {str(row["Field"]): str(row.get("Type") or "") for row in column_rows}
    ip_col = _pick_column(columns, IP_COLUMN_CANDIDATES)
    name_col = _pick_column(columns, NAME_COLUMN_CANDIDATES)
    vendor_col = _pick_column(columns, VENDOR_COLUMN_CANDIDATES)
    time_col = _pick_column(columns, RECENT_TIME_COLUMN_CANDIDATES)
    if not ip_col:
        return {}

    sql = (
        f"SELECT "
        f"{f'`{name_col}` AS device_name,' if name_col else 'NULL AS device_name,'}"
        f"{f'`{vendor_col}` AS vendor,' if vendor_col else 'NULL AS vendor,'}"
        f"{f'`{time_col}` AS recent_time ' if time_col else 'NULL AS recent_time '}"
        f"FROM `{IPADDRESS_TABLE_NAME}` "
        f"WHERE `{ip_col}` = %s "
    )
    if time_col:
        sql += f"ORDER BY `{time_col}` DESC LIMIT 1"
    else:
        sql += "LIMIT 1"
    cur.execute(sql, (device_ip,))
    row = cur.fetchone() or {}
    return {
        "device_name": str(row.get("device_name") or "").strip() or None,
        "vendor": _normalize_vendor(row.get("vendor")),
        "recent_time": _json_safe_value(row.get("recent_time")),
        "table": IPADDRESS_TABLE_NAME,
    }


def query_log_server(
    *,
    device_ip: str,
    device_name: str | None,
    user_start: datetime,
    user_end: datetime,
    context_lines: int,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_name: str | None = None,
) -> dict[str, Any]:
    query_start = user_start - timedelta(minutes=max(5, int(context_lines or 0)))
    query_end = user_end + timedelta(minutes=max(5, int(context_lines or 0)))
    device_names = _normalize_device_names(device_name)

    conn = connect_db(
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_name=db_name,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM `{LOG_TABLE_NAME}`")
            column_rows = cur.fetchall()
            if not column_rows:
                raise RuntimeError(f"Table {LOG_TABLE_NAME} has no columns")
            columns = {str(row["Field"]): str(row.get("Type") or "") for row in column_rows}

            time_col = _pick_column(columns, TIME_COLUMN_CANDIDATES)
            message_col = _pick_column(columns, MESSAGE_COLUMN_CANDIDATES)
            ip_col = _pick_column(columns, IP_COLUMN_CANDIDATES)
            name_col = _pick_column(columns, NAME_COLUMN_CANDIDATES)

            if not time_col or not message_col:
                raise RuntimeError(
                    f"Unable to map {LOG_TABLE_NAME} columns, found={', '.join(columns.keys())}"
                )
            if not ip_col and not name_col:
                raise RuntimeError(
                    f"Unable to find device IP/hostname column in {LOG_TABLE_NAME}, found={', '.join(columns.keys())}"
                )

            where_parts: list[str] = []
            params: list[Any] = []
            if ip_col and device_ip:
                where_parts.append(f"`{ip_col}` = %s")
                params.append(device_ip)
            if name_col and device_names:
                placeholders = ", ".join(["%s"] * len(device_names))
                where_parts.append(f"`{name_col}` IN ({placeholders})")
                params.extend(device_names)
            if not where_parts:
                raise RuntimeError("SQL log query requires device IP or device name")

            time_is_int = _is_integer_column(columns.get(time_col, ""))
            if time_is_int:
                time_clause = (
                    f"((`{time_col}` >= %s AND `{time_col}` <= %s) "
                    f"OR (`{time_col}` >= %s AND `{time_col}` <= %s))"
                )
                params.extend(
                    [
                        int(query_start.timestamp()),
                        int(query_end.timestamp()),
                        int(query_start.timestamp() * 1000),
                        int(query_end.timestamp() * 1000),
                        20000,
                    ]
                )
            else:
                time_clause = f"`{time_col}` >= %s AND `{time_col}` <= %s"
                params.extend(
                    [
                        _format_sql_time_value(query_start, False),
                        _format_sql_time_value(query_end, False),
                        20000,
                    ]
                )
            sql = f"""
                SELECT
                    `{time_col}` AS event_time_raw,
                    `{message_col}` AS message_raw,
                    {f'`{ip_col}` AS device_ip_raw,' if ip_col else 'NULL AS device_ip_raw,'}
                    {f'`{name_col}` AS device_name_raw' if name_col else 'NULL AS device_name_raw'}
                FROM `{LOG_TABLE_NAME}`
                WHERE ({' OR '.join(where_parts)})
                  AND {time_clause}
                ORDER BY `{time_col}` ASC
                LIMIT %s
            """
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            device_meta = _lookup_ipaddresslist_device(cur, device_ip)

        lines: list[str] = []
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        resolved_device_name = device_name
        for row in rows:
            row_dt = _coerce_row_time(row.get("event_time_raw"))
            if row_dt is not None:
                if first_ts is None or row_dt < first_ts:
                    first_ts = row_dt
                if last_ts is None or row_dt > last_ts:
                    last_ts = row_dt
            ts_text = row_dt.strftime("%Y-%m-%d %H:%M:%S") if row_dt else str(row.get("event_time_raw") or "")
            row_name = str(row.get("device_name_raw") or "").strip()
            if row_name and not resolved_device_name:
                resolved_device_name = row_name
            row_ip = str(row.get("device_ip_raw") or "").strip()
            prefix = row_name or row_ip or resolved_device_name or device_ip
            message = str(row.get("message_raw") or "").strip()
            line = f"{ts_text} [sql] {prefix} {message}".strip()
            lines.append(line)

        resolved_device_name = resolved_device_name or device_meta.get("device_name")
        resolved_vendor = device_meta.get("vendor") or "unknown"

        return {
            "raw_text": "\n".join(lines),
            "row_count": len(lines),
            "device_name": resolved_device_name,
            "vendor": resolved_vendor,
            "device_meta": device_meta,
            "time_col": time_col,
            "message_col": message_col,
            "ip_col": ip_col,
            "name_col": name_col,
            "query_start": query_start,
            "query_end": query_end,
            "first_ts": first_ts,
            "last_ts": last_ts,
        }
    finally:
        conn.close()
