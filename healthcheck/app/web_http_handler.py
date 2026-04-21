#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""HTTP handler layer extracted from web_server for easier page/UI maintenance."""

from app.web_server import *  # noqa: F401,F403
from app import web_server as web_server_module
from app.routers import pages as pages_router

class Handler(BaseHTTPRequestHandler):
    def _parse_cookie(self) -> Dict[str, str]:
        raw = self.headers.get("Cookie", "") or ""
        out: Dict[str, str] = {}
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def _current_user(self) -> Dict:
        return {"username": "", "role": "user", "can_modify": True}

    def _redirect(self, path: str, set_cookie: str = "") -> None:
        self.send_response(303)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Location", path)
        self.end_headers()

    def _require_login(self, lang: str) -> Dict:
        return self._current_user()

    def _require_admin(self, lang: str) -> Dict:
        self._redirect(with_lang("/", lang))
        return {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        lang = normalize_lang((query.get("lang", [""])[0] or "").strip())
        if parsed.path.startswith("/shared/"):
            self._serve_shared_asset(parsed.path[len("/shared/"):])
            return
        if parsed.path == "/logout":
            self._redirect(with_lang("/", lang))
            return
        if parsed.path in {"/login", "/admin"}:
            self._redirect(with_lang("/", lang))
            return

        user = self._current_user()
        if parsed.path == "/":
            self._respond_html(pages_router.render_home(lang=lang, user=user))
            return
        if parsed.path == "/tasks":
            self._respond_html(pages_router.render_tasks(lang=lang, user=user))
            return
        if parsed.path.startswith("/tasks/"):
            task_id = (parsed.path.split("/tasks/", 1)[1] or "").strip()
            if not task_id:
                self.send_error(400, "Missing task id")
                return
            self._respond_html(pages_router.render_task_detail(task_id=task_id, lang=lang, user=user))
            return
        if parsed.path == "/ai/settings":
            self._respond_html(pages_router.render_ai_settings(lang=lang, user=user))
            return
        if parsed.path == "/guide":
            self._respond_html(
                pages_router.render_user_guide(
                    lang=lang,
                    user=user,
                    doc_version=DOC_VERSION,
                    doc_version_rule=DOC_VERSION_RULE,
                )
            )
            return
        if parsed.path == "/guide/design":
            self._redirect(with_lang("/guide/user", lang))
            return
        if parsed.path == "/guide/design-zh":
            self._redirect(with_lang("/guide/user", "zh"))
            return
        if parsed.path == "/guide/design-en":
            self._redirect(with_lang("/guide/user", "en"))
            return
        if parsed.path == "/guide/user":
            self._respond_html(
                pages_router.render_user_guide(
                    lang=lang,
                    user=user,
                    doc_version=DOC_VERSION,
                    doc_version_rule=DOC_VERSION_RULE,
                )
            )
            return
        if parsed.path == "/guide/user-zh":
            self._respond_html(
                pages_router.render_user_guide(
                    lang="zh",
                    user=user,
                    doc_version=DOC_VERSION,
                    doc_version_rule=DOC_VERSION_RULE,
                )
            )
            return
        if parsed.path == "/guide/user-en":
            self._respond_html(
                pages_router.render_user_guide(
                    lang="en",
                    user=user,
                    doc_version=DOC_VERSION,
                    doc_version_rule=DOC_VERSION_RULE,
                )
            )
            return
        if parsed.path == "/job":
            history_mode = (query.get("history", [""])[0] or "").strip() in {"1", "true", "yes", "on"}
            job_id = (query.get("id", [""])[0] or "").strip()
            if not job_id and not history_mode:
                self.send_error(400, "Missing job id")
                return
            self._respond_html(
                pages_router.render_history_job(job_id=job_id, lang=lang, user=user)
            )
            return
        if parsed.path == "/job_status":
            self._serve_job_status(parsed.query, lang=lang)
            return
        if parsed.path == "/analysis_status":
            self._serve_analysis_status(parsed.query)
            return
        if parsed.path == "/job_active_analysis":
            self._serve_job_active_analysis(parsed.query)
            return
        if parsed.path == "/download":
            self._serve_download(parsed.query)
            return
        if parsed.path == "/download_ai":
            self._serve_ai_download(parsed.query)
            return
        self.send_error(404, "Not Found")
        return

    def _serve_shared_asset(self, relpath: str) -> None:
        rel = (relpath or "").lstrip("/")
        if not rel or ".." in rel.split("/"):
            self.send_error(400, "Invalid static asset")
            return
        target = SHARED_DIR / rel
        if not target.is_file():
            self.send_error(404, "Not Found")
            return
        data = target.read_bytes()
        ctype, _ = mimetypes.guess_type(target.name)
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _serve_download(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        name = (query.get("name", [""])[0] or "").strip()
        if not is_safe_report_name(name):
            self.send_error(400, "Invalid report name")
            return

        target = REPORT_DIR / name
        if not target.is_file():
            self.send_error(404, "Not Found")
            return

        data = target.read_bytes()
        ctype, _ = mimetypes.guess_type(target.name)
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _serve_ai_download(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        name = (query.get("name", [""])[0] or "").strip()
        if not is_safe_ai_report_name(name):
            self.send_error(400, "Invalid report name")
            return
        target = AI_REPORT_DIR / name
        if not target.is_file():
            self.send_error(404, "Not Found")
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _parse_task_ids(self, form: cgi.FieldStorage) -> List[str]:
        def _to_text(v) -> str:
            if v is None:
                return ""
            if hasattr(v, "value"):
                try:
                    vv = getattr(v, "value")
                    if vv is not None:
                        return str(vv)
                except Exception:
                    pass
            if isinstance(v, bytes):
                try:
                    return v.decode("utf-8", errors="ignore")
                except Exception:
                    return ""
            return str(v)

        ids: List[str] = []
        raw_values: List[str] = []
        try:
            vals = form.getlist("task_ids")
            if isinstance(vals, list):
                for v in vals:
                    if v is None:
                        continue
                    if isinstance(v, (list, tuple)):
                        for x in v:
                            txt = _to_text(x).strip()
                            if txt:
                                raw_values.append(txt)
                    else:
                        txt = _to_text(v).strip()
                        if txt:
                            raw_values.append(txt)
        except Exception:
            pass
        if not raw_values:
            raw_one = form.getvalue("task_ids")
            if isinstance(raw_one, (list, tuple)):
                for x in raw_one:
                    txt = _to_text(x).strip()
                    if txt:
                        raw_values.append(txt)
            elif raw_one is not None:
                txt = _to_text(raw_one).strip()
                if txt:
                    raw_values.append(txt)
        for raw in raw_values:
            for part in raw.replace(";", ",").split(","):
                tid = part.strip()
                if tid.startswith("MiniFieldStorage(") and "'" in tid:
                    m = re.search(r"MiniFieldStorage\(\s*'[^']+'\s*,\s*'([^']+)'\s*\)", tid)
                    if m:
                        tid = m.group(1).strip()
                if tid:
                    ids.append(tid)
        dedup: List[str] = []
        seen = set()
        for tid in ids:
            if tid in seen:
                continue
            seen.add(tid)
            dedup.append(tid)
        return dedup

    def _cleanup_task_files(self, task_row: Dict) -> int:
        removed = 0
        report_json = str((task_row or {}).get("report_json", "") or "").strip()
        report_csv = str((task_row or {}).get("report_csv", "") or "").strip()
        for name in [report_json, report_csv]:
            if not name:
                continue
            safe_name = os.path.basename(name)
            if safe_name != name:
                continue
            path = REPORT_DIR / safe_name
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except Exception:
                pass
        task_id = str((task_row or {}).get("task_id", "") or "").strip()
        if task_id:
            for p in list_ai_report_files(task_id, limit=200, include_legacy=True):
                try:
                    if p.is_file():
                        p.unlink()
                        removed += 1
                except Exception:
                    pass
        return removed

    def _handle_delete_tasks(self, form: cgi.FieldStorage) -> None:
        ids = self._parse_task_ids(form)
        if not ids:
            self._respond_json({"ok": False, "error": "Missing task_ids"}, status=400)
            return
        ensure_task_store()
        task_store_obj = getattr(web_server_module, "TASK_STORE", None)
        deleted_tasks = 0
        removed_files = 0
        for tid in ids:
            row = task_store_obj.get_task(tid) if task_store_obj else None
            if row:
                removed_files += self._cleanup_task_files(row)
            deleted_ok = False
            if task_store_obj:
                try:
                    if hasattr(task_store_obj, "delete_task"):
                        task_store_obj.delete_task(tid)
                    else:
                        with task_store_obj._lock:  # type: ignore[attr-defined]
                            task_store_obj._conn.execute("DELETE FROM tasks WHERE task_id = ?", (tid,))  # type: ignore[attr-defined]
                            task_store_obj._conn.commit()  # type: ignore[attr-defined]
                    deleted_ok = task_store_obj.get_task(tid) is None
                except Exception:
                    deleted_ok = False
            if deleted_ok:
                deleted_tasks += 1
            with JOBS_LOCK:
                JOBS.pop(tid, None)
            with ANALYSIS_JOBS_LOCK:
                stale_ids = [aid for aid, payload in ANALYSIS_JOBS.items() if str(payload.get("job_id", "") or "").strip() == tid]
                for aid in stale_ids:
                    ANALYSIS_JOBS.pop(aid, None)
        self._respond_json(
            {
                "ok": True,
                "deleted_tasks": deleted_tasks,
                "requested_tasks": len(ids),
                "removed_files": removed_files,
            }
        )

    def _serve_job_status(self, raw_query: str, lang: str = "zh") -> None:
        query = parse_qs(raw_query)
        job_id = (query.get("id", [""])[0] or "").strip()
        if not job_id:
            self.send_error(400, "Missing job id")
            return

        ensure_task_store()
        task_store_obj = getattr(web_server_module, "TASK_STORE", None)
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                row = task_store_obj.get_task(job_id) if task_store_obj else None
                if row:
                    payload = {
                        "status": row.get("status", "unknown"),
                        "exit_code": row.get("exit_code"),
                        "output": row.get("output_text", "") or "",
                        "report_json": row.get("report_json", "") or "",
                        "report_csv": row.get("report_csv", "") or "",
                    }
                else:
                    payload = {
                        "status": "error",
                        "exit_code": -1,
                        "output": "Task not found or expired" if normalize_lang(lang) == "en" else "任务不存在或已过期",
                    }
            else:
                payload = {
                    "status": job.get("status", "error"),
                    "exit_code": job.get("exit_code"),
                    "output": job.get("output", ""),
                    "report_json": job.get("report_json", ""),
                    "report_csv": job.get("report_csv", ""),
                }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_analysis_status(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        analysis_id = (query.get("id", [""])[0] or "").strip()
        if not analysis_id:
            self.send_error(400, "Missing analysis id")
            return
        ensure_analysis_services()
        analysis_status_store = getattr(web_server_module, "ANALYSIS_STATUS_STORE", None)
        payload = analysis_status_store.get_response_payload(analysis_id) if analysis_status_store else {"ok": False, "error": "analysis status service unavailable"}
        self._respond_json(payload)

    def _serve_job_active_analysis(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        job_id = (query.get("id", [""])[0] or "").strip()
        if not job_id:
            self._respond_json({"ok": False, "error": "Missing job id"}, status=400)
            return
        ensure_analysis_services()
        analysis_status_store = getattr(web_server_module, "ANALYSIS_STATUS_STORE", None)
        if not analysis_status_store:
            self._respond_json({"ok": False, "error": "analysis status service unavailable"}, status=500)
            return
        analysis_id = analysis_status_store.find_analysis_id_by_job(job_id, running_only=True)
        if not analysis_id:
            self._respond_json({"ok": True, "active": False})
            return
        payload = analysis_status_store.get_response_payload(analysis_id)
        payload["analysis_id"] = analysis_id
        payload["active"] = bool(payload.get("ok")) and str(payload.get("status", "")) == "running"
        self._respond_json(payload)

    def _handle_analysis_stop(self, form: cgi.FieldStorage) -> None:
        analysis_id = (form.getvalue("analysis_id") or "").strip()
        job_id = (form.getvalue("job_id") or "").strip()
        ensure_analysis_services()
        analysis_status_store = getattr(web_server_module, "ANALYSIS_STATUS_STORE", None)
        if not analysis_status_store:
            self._respond_json({"ok": False, "error": "analysis status service unavailable"}, status=500)
            return
        if not analysis_id and job_id:
            analysis_id = analysis_status_store.find_analysis_id_by_job(job_id, running_only=True)
        if not analysis_id:
            self._respond_json({"ok": False, "error": "No running analysis task"}, status=404)
            return
        stopped = analysis_status_store.request_cancel(analysis_id)
        if not stopped:
            self._respond_json({"ok": False, "error": "Analysis task is not running", "analysis_id": analysis_id}, status=409)
            return
        self._respond_json(
            {
                "ok": True,
                "analysis_id": analysis_id,
                "message": "已请求停止分析，等待当前调用结束...",
            }
        )

    def _respond_json(self, payload: Dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_analysis_input(self, job: Dict) -> str:
        ensure_analysis_services()
        analysis_srv = getattr(web_server_module, "ANALYSIS_SERVICE", None)
        if not analysis_srv:
            raise RuntimeError("analysis service unavailable")
        return analysis_srv.build_analysis_input(job)

    def _llm_model_used(self, llm: Dict[str, str]) -> str:
        ensure_analysis_services()
        analysis_srv = getattr(web_server_module, "ANALYSIS_SERVICE", None)
        if not analysis_srv:
            return ""
        return analysis_srv.model_used(llm)

    def _run_llm_analysis(self, llm: Dict[str, str], report_text: str) -> Tuple[str, Dict]:
        ensure_analysis_services()
        analysis_srv = getattr(web_server_module, "ANALYSIS_SERVICE", None)
        if not analysis_srv:
            raise RuntimeError("analysis service unavailable")
        return analysis_srv.run_llm_analysis(llm, report_text)

    def _load_job_report_json(self, job: Dict) -> Dict:
        ensure_analysis_services()
        analysis_srv = getattr(web_server_module, "ANALYSIS_SERVICE", None)
        if not analysis_srv:
            raise RuntimeError("analysis service unavailable")
        return analysis_srv.load_job_report_json(job)

    def _start_batched_analysis(
        self,
        job_id: str,
        llm: Dict[str, str],
        batch_size: int = 5,
        analysis_parallelism: int = 2,
        analysis_retries: int = 1,
        report_data_override: Optional[Dict] = None,
        large_report_mode: bool = False,
        large_report_chunk_items: int = 4,
    ) -> str:
        ensure_analysis_services()
        analysis_srv = getattr(web_server_module, "ANALYSIS_SERVICE", None)
        if not analysis_srv:
            raise RuntimeError("analysis service unavailable")
        return analysis_srv.start_batched_analysis(
            job_id=job_id,
            llm=llm,
            batch_size=batch_size,
            analysis_parallelism=analysis_parallelism,
            analysis_retries=analysis_retries,
            report_data_override=report_data_override,
            large_report_mode=large_report_mode,
            large_report_chunk_items=large_report_chunk_items,
        )

    def _handle_save_gpt_key(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "chatgpt").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
            provider = "chatgpt"
        chatgpt_model = (form.getvalue("chatgpt_model") or DEFAULT_GPT_MODEL).strip()
        local_base_url = (form.getvalue("local_base_url") or DEFAULT_LOCAL_BASE_URL).strip()
        local_model = (form.getvalue("local_model") or DEFAULT_LOCAL_MODEL).strip()
        deepseek_model = (form.getvalue("deepseek_model") or DEFAULT_DEEPSEEK_MODEL).strip()
        gemini_model = (form.getvalue("gemini_model") or DEFAULT_GEMINI_MODEL).strip()
        nvidia_model = (form.getvalue("nvidia_model") or DEFAULT_NVIDIA_MODEL).strip()
        selected_system_prompt = (form.getvalue("selected_system_prompt") or "").strip()
        selected_task_prompt = (form.getvalue("selected_task_prompt") or "").strip()
        if provider == "chatgpt" and not chatgpt_model:
            self._respond_json({"ok": False, "error": "chatgpt_model required"}, status=400)
            return
        if provider == "local" and (not local_base_url or not local_model):
            self._respond_json({"ok": False, "error": "local_base_url/local_model required"}, status=400)
            return
        if provider == "deepseek" and not deepseek_model:
            self._respond_json({"ok": False, "error": "deepseek_model required"}, status=400)
            return
        if provider == "gemini" and not gemini_model:
            self._respond_json({"ok": False, "error": "gemini_model required"}, status=400)
            return
        if provider == "nvidia" and not nvidia_model:
            self._respond_json({"ok": False, "error": "nvidia_model required"}, status=400)
            return
        cfg = load_gpt_config()
        cfg["provider"] = provider
        cfg["chatgpt_model"] = chatgpt_model
        cfg["local_base_url"] = local_base_url
        cfg["local_model"] = local_model
        cfg["deepseek_model"] = deepseek_model
        cfg["gemini_model"] = gemini_model
        cfg["nvidia_model"] = nvidia_model
        cfg["selected_system_prompt"] = selected_system_prompt
        cfg["selected_task_prompt"] = selected_task_prompt
        save_gpt_config(cfg)
        self._respond_json({"ok": True})

    def _handle_save_api_key(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        api_key = (form.getvalue("api_key") or "").strip()
        if provider not in {"chatgpt", "deepseek", "gemini", "nvidia"}:
            self._respond_json({"ok": False, "error": "provider must be chatgpt/deepseek/gemini/nvidia"}, status=400)
            return
        if not api_key:
            self._respond_json({"ok": False, "error": "API Key is empty"}, status=400)
            return
        cfg = load_gpt_config()
        key_field_map = {
            "chatgpt": "chatgpt_api_key",
            "deepseek": "deepseek_api_key",
            "gemini": "gemini_api_key",
            "nvidia": "nvidia_api_key",
        }
        key_field = key_field_map[provider]
        overwritten = bool(str(cfg.get(key_field, "") or "").strip())
        cfg[key_field] = api_key
        save_gpt_config(cfg)
        self._respond_json(
            {
                "ok": True,
                "overwritten": overwritten,
                "has_chatgpt_key": bool(str(cfg.get("chatgpt_api_key", "") or "").strip()),
                "has_deepseek_key": bool(str(cfg.get("deepseek_api_key", "") or "").strip()),
                "has_gemini_key": bool(str(cfg.get("gemini_api_key", "") or "").strip()),
                "has_nvidia_key": bool(str(cfg.get("nvidia_api_key", "") or "").strip()),
            }
        )

    def _handle_import_prompt(self, form: cgi.FieldStorage) -> None:
        upload = form["prompt_file"] if "prompt_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "Prompt file is required"}, status=400)
            return
        prompt_kind = (form.getvalue("prompt_kind") or "task").strip().lower()
        if prompt_kind not in {"task", "system"}:
            prompt_kind = "task"
        raw_name = (form.getvalue("prompt_name") or "").strip()
        if not raw_name:
            raw_name = Path(str(upload.filename)).stem
        prompt_name = sanitize_prompt_name(raw_name)
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        raw = upload.file.read()
        if not raw:
            self._respond_json({"ok": False, "error": "Prompt file is empty"}, status=400)
            return
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="ignore")
        text = text.strip()
        if not text:
            self._respond_json({"ok": False, "error": "Prompt file has no valid text"}, status=400)
            return

        cfg = load_gpt_config()
        target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
        if not write_prompt_file(target_dir, prompt_name, text):
            self._respond_json({"ok": False, "error": "提示词模板保存失败"}, status=500)
            return
        cfg["custom_prompts"] = {}
        save_gpt_config(cfg)
        prompts = merged_system_prompt_catalog() if prompt_kind == "system" else merged_task_prompt_catalog()
        self._respond_json(
            {
                "ok": True,
                "prompt_kind": prompt_kind,
                "prompts": prompts,
                "selected_prompt": prompt_name,
            }
        )

    def _handle_update_prompt(self, form: cgi.FieldStorage) -> None:
        prompt_kind = (form.getvalue("prompt_kind") or "task").strip().lower()
        if prompt_kind not in {"task", "system"}:
            prompt_kind = "task"
        raw_name = (form.getvalue("prompt_name") or "").strip()
        prompt_name = sanitize_prompt_name(raw_name)
        prompt_text = (form.getvalue("prompt_text") or "").strip()
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        if not prompt_text:
            self._respond_json({"ok": False, "error": "Prompt text is empty"}, status=400)
            return
        target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
        if not write_prompt_file(target_dir, prompt_name, prompt_text):
            self._respond_json({"ok": False, "error": "提示词保存失败"}, status=500)
            return
        prompts = prompt_catalog_by_kind(prompt_kind)
        self._respond_json(
            {
                "ok": True,
                "prompt_kind": prompt_kind,
                "prompts": prompts,
                "selected_prompt": prompt_name,
            }
        )

    def _handle_delete_prompt(self, form: cgi.FieldStorage) -> None:
        prompt_kind = (form.getvalue("prompt_kind") or "task").strip().lower()
        if prompt_kind not in {"task", "system"}:
            prompt_kind = "task"
        raw_name = (form.getvalue("prompt_name") or "").strip()
        prompt_name = sanitize_prompt_name(raw_name)
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
        target_file = target_dir / prompt_file_name(prompt_name)
        if not target_file.is_file():
            self._respond_json({"ok": False, "error": "仅可删除自定义模板，默认模板不可直接删除"}, status=400)
            return
        try:
            target_file.unlink()
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"删除失败: {exc}"}, status=500)
            return
        prompts = prompt_catalog_by_kind(prompt_kind)
        fallback_selected = "网络工程师-严格模式" if prompt_kind == "system" else ""
        self._respond_json(
            {
                "ok": True,
                "prompt_kind": prompt_kind,
                "prompts": prompts,
                "selected_prompt": fallback_selected if prompt_name not in prompts else prompt_name,
            }
        )

    def _handle_import_check_template(self, form: cgi.FieldStorage) -> None:
        upload = form["template_file"] if "template_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "Template file is required"}, status=400)
            return
        raw_name = (form.getvalue("template_name") or "").strip()
        if not raw_name:
            raw_name = Path(str(upload.filename)).stem
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可覆盖"}, status=400)
            return
        raw = upload.file.read()
        if not raw:
            self._respond_json({"ok": False, "error": "Template file is empty"}, status=400)
            return
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="ignore")
        checks, commands = parse_check_template_text(text)
        if not checks and not commands:
            self._respond_json({"ok": False, "error": "No valid checks in file"}, status=400)
            return
        if not write_check_template_file(CHECK_CUSTOM_TEMPLATES_DIR, template_name, checks, commands):
            self._respond_json({"ok": False, "error": "检查项模板保存失败"}, status=500)
            return
        templates = merged_check_template_catalog()
        self._respond_json({"ok": True, "templates": templates, "selected_template": template_name})

    def _handle_update_check_template(self, form: cgi.FieldStorage) -> None:
        raw_name = (form.getvalue("template_name") or "").strip()
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可修改"}, status=400)
            return
        template_text = (form.getvalue("template_text") or "").strip()
        checks, commands = parse_check_template_text(template_text)
        if not checks and not commands:
            self._respond_json({"ok": False, "error": "Template text is empty"}, status=400)
            return
        if not write_check_template_file(CHECK_CUSTOM_TEMPLATES_DIR, template_name, checks, commands):
            self._respond_json({"ok": False, "error": "检查项模板保存失败"}, status=500)
            return
        templates = merged_check_template_catalog()
        self._respond_json({"ok": True, "templates": templates, "selected_template": template_name})

    def _handle_save_check_template_from_selection(self, form: cgi.FieldStorage) -> None:
        raw_name = (form.getvalue("template_name") or "").strip()
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可覆盖"}, status=400)
            return
        allow_overwrite = (form.getvalue("allow_overwrite") or "").strip().lower() in {"1", "true", "y", "yes", "on"}
        checks = parse_check_items(form.getvalue("checks_text") or "")
        commands = parse_ordered_items(form.getvalue("commands_text") or "")
        if not checks and not commands:
            self._respond_json({"ok": False, "error": "Template content is empty"}, status=400)
            return
        target_file = CHECK_CUSTOM_TEMPLATES_DIR / check_template_file_name(template_name)
        if target_file.is_file() and not allow_overwrite:
            self._respond_json({"ok": False, "error": "template_exists"}, status=409)
            return
        if not write_check_template_file(CHECK_CUSTOM_TEMPLATES_DIR, template_name, checks, commands):
            self._respond_json({"ok": False, "error": "检查项模板保存失败"}, status=500)
            return
        templates = merged_check_template_catalog()
        self._respond_json({"ok": True, "templates": templates, "selected_template": template_name})

    def _handle_delete_check_template(self, form: cgi.FieldStorage) -> None:
        raw_name = (form.getvalue("template_name") or "").strip()
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可删除"}, status=400)
            return
        target = CHECK_CUSTOM_TEMPLATES_DIR / check_template_file_name(template_name)
        if not target.is_file():
            self._respond_json({"ok": False, "error": "仅可删除自定义模板"}, status=400)
            return
        try:
            target.unlink()
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"删除失败: {exc}"}, status=500)
            return
        templates = merged_check_template_catalog()
        fallback = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")
        self._respond_json({"ok": True, "templates": templates, "selected_template": fallback})

    def _handle_test_llm(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        local_base_url = (form.getvalue("local_base_url") or "").strip()
        deepseek_model = (form.getvalue("deepseek_model") or "").strip()
        gemini_model = (form.getvalue("gemini_model") or "").strip()
        nvidia_model = (form.getvalue("nvidia_model") or "").strip()
        cfg = load_gpt_config()
        if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
            provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
            if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
                provider = "chatgpt"

        try:
            if provider == "local":
                if not local_base_url:
                    local_base_url = str(cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
                msg = test_local_lmstudio_connection(local_base_url)
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: N/A（本地模型）",
                        "provider_used": "local",
                        "token_balance_status": "n/a",
                        "token_balance_message": "N/A（本地模型）",
                    }
                )
                return

            if provider == "deepseek":
                api_key = str(cfg.get("deepseek_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "DeepSeek API Key not set"}, status=400)
                    return
                msg = test_deepseek_connection(api_key)
                bal_state, bal_msg = self._probe_cloud_token_balance(
                    provider="deepseek",
                    api_key=api_key,
                    model=(deepseek_model or str(cfg.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL).strip()),
                )
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: {bal_msg}",
                        "provider_used": "deepseek",
                        "token_balance_status": bal_state,
                        "token_balance_message": bal_msg,
                    }
                )
                return

            if provider == "gemini":
                api_key = str(cfg.get("gemini_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "Gemini API Key not set"}, status=400)
                    return
                msg = test_gemini_connection(api_key)
                bal_state, bal_msg = self._probe_cloud_token_balance(
                    provider="gemini",
                    api_key=api_key,
                    model=(gemini_model or str(cfg.get("gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL).strip()),
                )
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: {bal_msg}",
                        "provider_used": "gemini",
                        "token_balance_status": bal_state,
                        "token_balance_message": bal_msg,
                    }
                )
                return

            if provider == "nvidia":
                api_key = str(cfg.get("nvidia_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "NVIDIA API Key not set"}, status=400)
                    return
                msg = test_nvidia_connection(api_key)
                bal_state, bal_msg = self._probe_cloud_token_balance(
                    provider="nvidia",
                    api_key=api_key,
                    model=(nvidia_model or str(cfg.get("nvidia_model", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL).strip()),
                )
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: {bal_msg}",
                        "provider_used": "nvidia",
                        "token_balance_status": bal_state,
                        "token_balance_message": bal_msg,
                    }
                )
                return

            api_key = str(cfg.get("chatgpt_api_key", "") or "").strip()
            if not api_key:
                self._respond_json({"ok": False, "error": "ChatGPT API Key not set"}, status=400)
                return
            msg = test_openai_connection(api_key)
            bal_state, bal_msg = self._probe_cloud_token_balance(
                provider="chatgpt",
                api_key=api_key,
                model=str(cfg.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL).strip(),
            )
            self._respond_json(
                {
                    "ok": True,
                    "message": f"{msg} | Token余额: {bal_msg}",
                    "provider_used": "chatgpt",
                    "token_balance_status": bal_state,
                    "token_balance_message": bal_msg,
                }
            )
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)

    def _probe_cloud_token_balance(self, provider: str, api_key: str, model: str) -> Tuple[str, str]:
        ensure_analysis_services()
        analysis_srv = getattr(web_server_module, "ANALYSIS_SERVICE", None)
        if not analysis_srv:
            return "unknown", "未知（analysis service unavailable）"
        return analysis_srv.probe_cloud_token_balance(provider, api_key, model)

    def _resolve_llm_inputs_from_form(self, form: cgi.FieldStorage) -> Dict[str, str]:
        cfg = load_gpt_config()
        provider = (form.getvalue("provider") or "").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
            provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
            if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
                provider = "chatgpt"
        local_base_url = (form.getvalue("local_base_url") or "").strip() or str(
            cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL
        ).strip()
        chatgpt_model = (form.getvalue("chatgpt_model") or "").strip() or str(
            cfg.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL
        ).strip()
        local_model = (form.getvalue("local_model") or "").strip() or str(
            cfg.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL
        ).strip()
        deepseek_model = (form.getvalue("deepseek_model") or "").strip() or str(
            cfg.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL
        ).strip()
        gemini_model = (form.getvalue("gemini_model") or "").strip() or str(
            cfg.get("gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL
        ).strip()
        nvidia_model = (form.getvalue("nvidia_model") or "").strip() or str(
            cfg.get("nvidia_model", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL
        ).strip()
        if provider == "deepseek":
            api_key = str(cfg.get("deepseek_api_key", "") or "").strip()
        elif provider == "gemini":
            api_key = str(cfg.get("gemini_api_key", "") or "").strip()
        elif provider == "nvidia":
            api_key = str(cfg.get("nvidia_api_key", "") or "").strip()
        elif provider == "chatgpt":
            api_key = str(cfg.get("chatgpt_api_key", "") or "").strip()
        else:
            api_key = ""
        system_prompt_key = (form.getvalue("system_prompt_key") or "").strip()
        task_prompt_key = (form.getvalue("prompt_key") or "").strip()
        system_prompt_extra = (form.getvalue("system_prompt_extra") or "").strip()
        task_prompt_extra = (form.getvalue("custom_prompt") or "").strip()

        system_prompts = merged_system_prompt_catalog()
        task_prompts = merged_task_prompt_catalog()

        base_system_prompt = (
            system_prompts.get(system_prompt_key, "")
            if system_prompt_key
            else system_prompts.get("网络工程师-严格模式", "")
        )
        base_task_prompt = task_prompts.get(task_prompt_key, "") if task_prompt_key else ""

        if base_system_prompt and system_prompt_extra:
            system_prompt_text = f"{base_system_prompt}\n\n[Extra System Constraints]\n{system_prompt_extra}"
            system_prompt_source = f"system_template+extra:{system_prompt_key or '网络工程师-严格模式'}"
        elif base_system_prompt:
            system_prompt_text = base_system_prompt
            system_prompt_source = f"system_template:{system_prompt_key or '网络工程师-严格模式'}"
        elif system_prompt_extra:
            system_prompt_text = system_prompt_extra
            system_prompt_source = "system_extra_only"
        else:
            system_prompt_text = DEFAULT_SYSTEM_PROMPTS["网络工程师-严格模式"]
            system_prompt_source = "system_default:网络工程师-严格模式"

        if base_task_prompt and task_prompt_extra:
            task_prompt_text = f"{base_task_prompt}\n\n[Extra Task Requirements]\n{task_prompt_extra}"
            task_prompt_source = f"task_template+extra:{task_prompt_key}"
        elif base_task_prompt:
            task_prompt_text = base_task_prompt
            task_prompt_source = f"task_template:{task_prompt_key}"
        elif task_prompt_extra:
            task_prompt_text = task_prompt_extra
            task_prompt_source = "task_extra_only"
        else:
            task_prompt_text = DEFAULT_TASK_PROMPTS["基础巡检诊断"]
            task_prompt_source = "task_default:基础巡检诊断"
        return {
            "provider": provider,
            "api_key": api_key,
            "chatgpt_model": chatgpt_model,
            "local_base_url": local_base_url,
            "local_model": local_model,
            "deepseek_model": deepseek_model,
            "gemini_model": gemini_model,
            "nvidia_model": nvidia_model,
            "system_prompt_text": system_prompt_text,
            "task_prompt_text": task_prompt_text,
            "system_prompt_key": system_prompt_key or "网络工程师-严格模式",
            "task_prompt_key": task_prompt_key,
            "prompt_source": f"{system_prompt_source}; {task_prompt_source}",
        }

    def _parse_analysis_options(self, form: cgi.FieldStorage) -> Dict[str, int]:
        batched_analysis = (form.getvalue("batched_analysis") or "").strip().lower() in {"1", "true", "on", "yes"}
        large_report_mode = (form.getvalue("large_report_mode") or "").strip().lower() in {"1", "true", "on", "yes"}
        analysis_parallelism_raw = (form.getvalue("analysis_parallelism") or "2").strip()
        analysis_retries_raw = (form.getvalue("analysis_retries") or "1").strip()
        large_report_chunk_items_raw = (form.getvalue("large_report_chunk_items") or "4").strip()
        try:
            analysis_parallelism = max(1, min(8, int(analysis_parallelism_raw or "2")))
        except ValueError:
            analysis_parallelism = 2
        try:
            analysis_retries = max(0, min(3, int(analysis_retries_raw or "1")))
        except ValueError:
            analysis_retries = 1
        try:
            large_report_chunk_items = max(1, min(20, int(large_report_chunk_items_raw or "4")))
        except ValueError:
            large_report_chunk_items = 4
        if large_report_mode and not batched_analysis:
            batched_analysis = True
        batch_size = max(1, min(50, analysis_parallelism))
        return {
            "batched_analysis": 1 if batched_analysis else 0,
            "large_report_mode": 1 if large_report_mode else 0,
            "analysis_parallelism": analysis_parallelism,
            "analysis_retries": analysis_retries,
            "large_report_chunk_items": large_report_chunk_items,
            "batch_size": batch_size,
        }

    def _precheck_report_data_from_job(self, job_id: str) -> Dict:
        job = self._resolve_job_for_analysis(job_id)
        if not job:
            raise RuntimeError("job not found")
        return self._load_job_report_json(job)

    def _resolve_job_for_analysis(self, job_id: str) -> Optional[Dict]:
        task_id = str(job_id or "").strip()
        if not task_id:
            return None
        with JOBS_LOCK:
            job = JOBS.get(task_id)
        if isinstance(job, dict):
            return dict(job)
        ensure_task_store()
        task_store_obj = getattr(web_server_module, "TASK_STORE", None)
        row = task_store_obj.get_task(task_id) if task_store_obj else None
        if not row:
            return None
        return {
            "status": str(row.get("status", "unknown") or "unknown"),
            "exit_code": row.get("exit_code"),
            "output": str(row.get("output_text", "") or ""),
            "report_json": str(row.get("report_json", "") or ""),
            "report_csv": str(row.get("report_csv", "") or ""),
        }

    def _handle_analysis_precheck(self, form: cgi.FieldStorage) -> None:
        llm = self._resolve_llm_inputs_from_form(form)
        opts = self._parse_analysis_options(form)
        source = (form.getvalue("source") or "").strip().lower()
        report_data: Dict = {}
        source_desc = ""

        if source == "history":
            upload = form["report_file"] if "report_file" in form else None
            if upload is None or not getattr(upload, "filename", ""):
                self._respond_json({"ok": False, "error": "report_file is required for history precheck"}, status=400)
                return
            try:
                _filename, raw = read_uploaded_report_raw(upload)
                maybe = json.loads(decode_best_effort_text(raw))
                if not isinstance(maybe, dict):
                    raise RuntimeError("invalid JSON root")
                report_data = maybe
                source_desc = "history_json"
            except Exception as exc:
                self._respond_json({"ok": False, "error": f"历史报告预估仅支持结构化 JSON：{exc}"}, status=400)
                return
        else:
            job_id = (form.getvalue("job_id") or "").strip()
            if not job_id:
                self._respond_json({"ok": False, "error": "job_id is required"}, status=400)
                return
            try:
                report_data = self._precheck_report_data_from_job(job_id)
                source_desc = "current_job_json"
            except Exception as exc:
                self._respond_json({"ok": False, "error": str(exc)}, status=400)
                return

        estimation = analysis_guard.estimate_analysis_plan(
            report_data=report_data,
            provider=llm.get("provider", "chatgpt"),
            batched=bool(opts["batched_analysis"]),
            parallelism=int(opts["analysis_parallelism"]),
            retries=int(opts["analysis_retries"]),
            large_report_mode=bool(opts["large_report_mode"]),
            large_report_chunk_items=int(opts["large_report_chunk_items"]),
            system_prompt_text=llm.get("system_prompt_text", ""),
            task_prompt_text=llm.get("task_prompt_text", ""),
        )
        self._respond_json(
            {
                "ok": True,
                "source": source_desc,
                "provider": llm.get("provider", "chatgpt"),
                "model_used": self._llm_model_used(llm),
                "estimation": estimation,
            }
        )

    def _handle_analyze_job(self, form: cgi.FieldStorage) -> None:
        job_id = (form.getvalue("job_id") or "").strip()
        try:
            llm = self._resolve_llm_inputs_from_form(form)
            opts = self._parse_analysis_options(form)
            batched_analysis = bool(opts["batched_analysis"])
            large_report_mode = bool(opts["large_report_mode"])
            analysis_parallelism = int(opts["analysis_parallelism"])
            analysis_retries = int(opts["analysis_retries"])
            large_report_chunk_items = int(opts["large_report_chunk_items"])
            batch_size = int(opts["batch_size"])
            cfg = load_gpt_config()
            cfg["selected_system_prompt"] = llm.get("system_prompt_key", "")
            cfg["selected_task_prompt"] = llm.get("task_prompt_key", "")
            save_gpt_config(cfg)
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"analysis init failed: {exc}"}, status=400)
            return

        if not job_id:
            self._respond_json({"ok": False, "error": "job_id is required"}, status=400)
            return
        job = self._resolve_job_for_analysis(job_id)
        if not job:
            self._respond_json({"ok": False, "error": "job not found"}, status=404)
            return

        if batched_analysis:
            report_data_override = None
            with JOBS_LOCK:
                in_memory_job = JOBS.get(job_id)
            if not in_memory_job:
                try:
                    report_data_override = self._load_job_report_json(job)
                except Exception as exc:
                    self._respond_json({"ok": False, "error": str(exc)}, status=400)
                    return
            analysis_id = self._start_batched_analysis(
                job_id,
                llm,
                batch_size=batch_size,
                analysis_parallelism=analysis_parallelism,
                analysis_retries=analysis_retries,
                report_data_override=report_data_override,
                large_report_mode=large_report_mode,
                large_report_chunk_items=large_report_chunk_items,
            )
            mode_desc = "分片模式" if large_report_mode else "标准分批模式"
            self._respond_json(
                {
                    "ok": True,
                    "async": True,
                    "analysis_id": analysis_id,
                    "message": (
                        f"已启动分批分析：{mode_desc}，AI并发={analysis_parallelism}，每设备分片数={large_report_chunk_items}，"
                        f"每轮设备数={batch_size}，"
                        f"重试={analysis_retries}"
                    ),
                }
            )
            return

        try:
            analysis_input = self._build_analysis_input(job)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=400)
            return
        started_at = time.time()
        try:
            analysis, usage = self._run_llm_analysis(llm, analysis_input)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        token_stats = add_token_usage(llm["provider"], int(usage.get("total_tokens", 0)))
        saved_name = save_ai_analysis_report(
            job_id,
            analysis_text=analysis,
            provider=llm["provider"],
            model=self._llm_model_used(llm),
            prompt_source=llm.get("prompt_source", ""),
            duration_seconds=max(0.0, time.time() - started_at),
            token_usage=usage,
            token_total=int(token_stats.get("total_tokens", 0)),
            source="task",
            status="done",
            error="",
        )
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": llm["provider"],
                "model_used": self._llm_model_used(llm),
                "local_base_url": llm["local_base_url"] if llm["provider"] == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
                "token_usage": usage,
                "token_total": int(token_stats.get("total_tokens", 0)),
                "duration_seconds": max(0.0, time.time() - started_at),
                "analysis_report_name": saved_name,
            }
        )

    def _handle_analyze_history_report(self, form: cgi.FieldStorage) -> None:
        upload = form["report_file"] if "report_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "report_file is required"}, status=400)
            return
        try:
            opts = self._parse_analysis_options(form)
            batched_analysis = bool(opts["batched_analysis"])
            large_report_mode = bool(opts["large_report_mode"])
            analysis_parallelism = int(opts["analysis_parallelism"])
            analysis_retries = int(opts["analysis_retries"])
            large_report_chunk_items = int(opts["large_report_chunk_items"])
            batch_size = int(opts["batch_size"])
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"analysis options invalid: {exc}"}, status=400)
            return

        filename = ""
        raw = b""
        try:
            filename, raw = read_uploaded_report_raw(upload)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=400)
            return
        text = decode_best_effort_text(raw)
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
        ratio = (printable / len(text)) if text else 0.0
        if not text or ratio < 0.65:
            b64 = base64.b64encode(raw).decode("ascii")[:200000]
            report_text = f"文件名: {filename}\n文件内容可能是二进制格式，以下为 base64 片段（已截断）：\n{b64}"
        else:
            report_text = f"文件名: {filename}\n文件文本内容（可能已截断）：\n{text[:200000]}"

        try:
            llm = self._resolve_llm_inputs_from_form(form)
            cfg = load_gpt_config()
            cfg["selected_system_prompt"] = llm.get("system_prompt_key", "")
            cfg["selected_task_prompt"] = llm.get("task_prompt_key", "")
            save_gpt_config(cfg)
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"analysis init failed: {exc}"}, status=400)
            return

        if batched_analysis:
            try:
                raw_text = decode_best_effort_text(raw)
                report_data = json.loads(raw_text)
                devices = report_data.get("devices", []) if isinstance(report_data, dict) else []
                if not isinstance(devices, list) or not devices:
                    raise RuntimeError("历史 JSON 报告中未找到 devices 列表")
                analysis_id = self._start_batched_analysis(
                    job_id="history_upload",
                    llm=llm,
                    batch_size=batch_size,
                    analysis_parallelism=analysis_parallelism,
                    analysis_retries=analysis_retries,
                    report_data_override=report_data,
                    large_report_mode=large_report_mode,
                    large_report_chunk_items=large_report_chunk_items,
                )
                mode_desc = "分片模式" if large_report_mode else "标准分批模式"
                self._respond_json(
                    {
                        "ok": True,
                        "async": True,
                        "analysis_id": analysis_id,
                        "message": (
                            f"历史 JSON 分批分析已启动：{mode_desc}，AI并发={analysis_parallelism}，"
                            f"每设备分片数={large_report_chunk_items}，每轮设备数={batch_size}，重试={analysis_retries}"
                        ),
                    }
                )
                return
            except Exception as exc:
                self._respond_json(
                    {
                        "ok": False,
                        "error": f"历史报告分批分析仅支持结构化 JSON 报告（含 devices），当前不满足: {exc}",
                    },
                    status=400,
                )
                return

        try:
            maybe_json = json.loads(decode_best_effort_text(raw))
            if isinstance(maybe_json, dict) and isinstance(maybe_json.get("devices", None), list):
                report_text = analysis_pipeline.build_whole_report_analysis_input(
                    maybe_json,
                    force_full=False,
                )
        except Exception:
            pass

        started_at = time.time()
        try:
            analysis, usage = self._run_llm_analysis(llm, report_text)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        token_stats = add_token_usage(llm["provider"], int(usage.get("total_tokens", 0)))
        saved_name = save_ai_analysis_report(
            "history",
            analysis_text=analysis,
            provider=llm["provider"],
            model=self._llm_model_used(llm),
            prompt_source=llm.get("prompt_source", ""),
            duration_seconds=max(0.0, time.time() - started_at),
            token_usage=usage,
            token_total=int(token_stats.get("total_tokens", 0)),
            source="history",
            status="done",
            error="",
        )
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": llm["provider"],
                "model_used": self._llm_model_used(llm),
                "local_base_url": llm["local_base_url"] if llm["provider"] == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
                "token_usage": usage,
                "token_total": int(token_stats.get("total_tokens", 0)),
                "duration_seconds": max(0.0, time.time() - started_at),
                "analysis_report_name": saved_name,
            }
        )

    def do_POST(self) -> None:
        try:
            self._do_post_impl()
        except BrokenPipeError:
            return
        except Exception as exc:
            try:
                self._respond_json({"ok": False, "error": f"server error: {exc}"}, status=500)
            except Exception:
                try:
                    self.send_error(500, "Internal Server Error")
                except Exception:
                    pass

    def _do_post_impl(self) -> None:
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ)
        if self.path in {"/login", "/admin/create_role", "/admin/create_user"}:
            self.send_error(404, "Not Found")
            return

        lang = normalize_lang((form.getvalue("lang") or "zh").strip())
        user = self._current_user()

        if self.path == "/save_gpt_key":
            self._handle_save_gpt_key(form)
            return
        if self.path == "/import_prompt":
            self._handle_import_prompt(form)
            return
        if self.path == "/update_prompt":
            self._handle_update_prompt(form)
            return
        if self.path == "/delete_prompt":
            self._handle_delete_prompt(form)
            return
        if self.path == "/import_check_template":
            self._handle_import_check_template(form)
            return
        if self.path == "/update_check_template":
            self._handle_update_check_template(form)
            return
        if self.path == "/delete_check_template":
            self._handle_delete_check_template(form)
            return
        if self.path == "/save_check_template_from_selection":
            self._handle_save_check_template_from_selection(form)
            return
        if self.path == "/save_api_key":
            self._handle_save_api_key(form)
            return
        if self.path == "/test_llm":
            self._handle_test_llm(form)
            return
        if self.path == "/analysis_precheck":
            self._handle_analysis_precheck(form)
            return
        if self.path == "/analysis_stop":
            self._handle_analysis_stop(form)
            return
        if self.path == "/delete_tasks":
            self._handle_delete_tasks(form)
            return
        if self.path == "/analyze_job":
            self._handle_analyze_job(form)
            return
        if self.path == "/analyze_history_report":
            self._handle_analyze_history_report(form)
            return
        if self.path != "/run":
            self.send_error(404, "Not Found")
            return

        username = (form.getvalue("username") or "").strip()
        password = (form.getvalue("password") or "").strip()
        lang = normalize_lang((form.getvalue("lang") or "zh").strip())
        manual_devices = (form.getvalue("devices") or "").strip()
        execution_mode = (form.getvalue("execution_mode") or "auto").strip().lower()
        if execution_mode not in {"serial", "parallel", "auto"}:
            execution_mode = "auto"
        parallel_workers = (form.getvalue("parallel_workers") or "").strip()
        if parallel_workers:
            try:
                parallel_workers = str(max(1, int(parallel_workers)))
            except ValueError:
                parallel_workers = ""
        connect_retry = (form.getvalue("connect_retry") or "0").strip()
        try:
            connect_retry = str(max(0, int(connect_retry)))
        except ValueError:
            connect_retry = "0"
        jump_mode = (form.getvalue("jump_mode") or "direct").strip().lower()
        if jump_mode not in {"direct", "ssh", "smc"}:
            jump_mode = "direct"
        jump_enabled = jump_mode in {"ssh", "smc"}
        jump_host = (form.getvalue("jump_host") or "").strip()
        jump_port = (form.getvalue("jump_port") or "22").strip() or "22"
        try:
            jump_port = str(max(1, int(jump_port)))
        except ValueError:
            jump_port = "22"
        jump_username = (form.getvalue("jump_username") or "").strip()
        jump_password = (form.getvalue("jump_password") or "").strip()
        smc_command = (form.getvalue("smc_command") or "smc server toc {jump_host}").strip() or "smc server toc {jump_host}"
        custom_commands = (form.getvalue("custom_commands") or "").strip()
        debug_mode = (form.getvalue("debug_mode") or "").strip() in {"1", "true", "y", "yes", "on"}
        check_template_key = (form.getvalue("check_template_key") or DEFAULT_CHECK_TEMPLATE_NAME).strip()
        selected = form.getlist("checks")
        selected = [item.strip() for item in selected if item and item.strip()]
        devices_upload = form["devices_file"] if "devices_file" in form else None
        imported_devices = ""
        if devices_upload is not None and getattr(devices_upload, "filename", "") and not manual_devices:
            raw_bytes = devices_upload.file.read()
            if raw_bytes:
                try:
                    imported_devices = raw_bytes.decode("utf-8-sig")
                except UnicodeDecodeError:
                    imported_devices = raw_bytes.decode("gb18030", errors="ignore")
        devices = normalize_inline_input(manual_devices or imported_devices)

        values = {
            "username": username,
            "password": password,
            "devices": manual_devices,
            "custom_commands": custom_commands,
            "execution_mode": execution_mode,
            "parallel_workers": parallel_workers,
            "connect_retry": connect_retry,
            "jump_mode": jump_mode,
            "jump_host": jump_host,
            "jump_port": jump_port,
            "jump_username": jump_username,
            "jump_password": jump_password,
            "smc_command": smc_command,
            "debug_mode": "1" if debug_mode else "",
        }
        templates = merged_check_template_catalog()
        if check_template_key not in templates:
            check_template_key = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")

        if not username or not password:
            self._respond_html(
                pages_router.render_home_with_state(
                    lang=lang,
                    user=user,
                    values=values,
                    selected_checks=selected,
                    status="ERROR: 用户名和密码不能为空",
                    selected_template=check_template_key,
                )
            )
            return
        if not devices:
            self._respond_html(
                pages_router.render_home_with_state(
                    lang=lang,
                    user=user,
                    values=values,
                    selected_checks=selected,
                    status="ERROR: 请输入设备地址或导入设备文件",
                    selected_template=check_template_key,
                )
            )
            return
        if not selected and not parse_ordered_items(custom_commands):
            self._respond_html(
                pages_router.render_home_with_state(
                    lang=lang,
                    user=user,
                    values=values,
                    selected_checks=selected,
                    status="ERROR: 请至少选择一个检查项或输入一条自定义命令",
                    selected_template=check_template_key,
                )
            )
            return
        if jump_mode == "ssh" and (not jump_host or not jump_username or not jump_password):
            self._respond_html(
                pages_router.render_home_with_state(
                    lang=lang,
                    user=user,
                    values=values,
                    selected_checks=selected,
                    status="ERROR: SSH 跳板模式时，跳板机地址/用户名/密码不能为空",
                    selected_template=check_template_key,
                )
            )
            return
        if jump_mode == "smc" and (not jump_host or not smc_command):
            self._respond_html(
                pages_router.render_home_with_state(
                    lang=lang,
                    user=user,
                    values=values,
                    selected_checks=selected,
                    status="ERROR: SMC 模式时，跳板机地址和 SMC 命令模板不能为空",
                    selected_template=check_template_key,
                )
            )
            return

        upload = form["command_map"] if "command_map" in form else None
        try:
            data = b""
            if upload is not None and getattr(upload, "filename", ""):
                data = upload.file.read()
                if not data:
                    self._respond_html(
                        pages_router.render_home_with_state(
                            lang=lang,
                            user=user,
                            values=values,
                            selected_checks=selected,
                            status="ERROR: 上传的 command_map 文件为空",
                            selected_template=check_template_key,
                        )
                    )
                    return
            else:
                default_map = COMMAND_MAP_PATH
                if not default_map.is_file():
                    self._respond_html(
                        pages_router.render_home_with_state(
                            lang=lang,
                            user=user,
                            values=values,
                            selected_checks=selected,
                            status="ERROR: 默认 config/command_map.yaml 不存在，请上传文件",
                            selected_template=check_template_key,
                        )
                    )
                    return
                data = default_map.read_bytes()

            job_id = start_job(
                username=username,
                password=password,
                devices=devices,
                selected=selected,
                custom_commands=custom_commands,
                map_bytes=data,
                debug_mode=debug_mode,
                execution_mode=execution_mode,
                parallel_workers=parallel_workers,
                connect_retry=connect_retry,
                jump_enabled=jump_enabled,
                jump_mode=jump_mode,
                jump_host=jump_host,
                jump_port=jump_port,
                jump_username=jump_username,
                jump_password=jump_password,
                smc_command=smc_command,
            )
            self.send_response(303)
            self.send_header("Location", with_lang(f"/job?id={job_id}", lang))
            self.end_headers()
        except Exception as exc:
            self._respond_html(
                pages_router.render_home_with_state(
                    lang=lang,
                    user=user,
                    values=values,
                    selected_checks=selected,
                    status=f"ERROR: {exc}",
                    selected_template=check_template_key,
                )
            )

    def _respond_html(self, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return
