#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from app.web_server import *  # noqa: F401,F403
from app import web_server as web_server_module

def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts or 0.0)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"

def build_tasks_page(lang: str = "zh", auth_username: str = "", auth_role: str = "user") -> str:
    lang = normalize_lang(lang)
    ensure_task_store()
    ensure_analysis_services()
    task_store_obj = getattr(web_server_module, "TASK_STORE", None)
    analysis_status_store = getattr(web_server_module, "ANALYSIS_STATUS_STORE", None)
    rows = task_store_obj.list_tasks(300) if task_store_obj else []
    h = ("选择", "任务ID", "创建时间", "状态", "AI 分析", "设备数", "报告", "操作")
    if lang == "en":
        h = ("Select", "Task ID", "Created At", "Status", "AI Analysis", "Devices", "Reports", "Actions")
    body_rows = []
    for row in rows:
        tid = str(row.get("task_id", "") or "")
        devices = row.get("devices", []) if isinstance(row.get("devices"), list) else []
        report_json = str(row.get("report_json", "") or "")
        report_csv = str(row.get("report_csv", "") or "")
        links = []
        if report_json:
            links.append(f'<a href="{with_lang("/download?name=" + report_json, lang)}">JSON</a>')
        if report_csv:
            links.append(f'<a href="{with_lang("/download?name=" + report_csv, lang)}">CSV</a>')
        if not links:
            links.append("-")
        ai_cell = "-"
        if analysis_status_store:
            analysis_id = analysis_status_store.find_analysis_id_by_job(tid, running_only=False)
            if analysis_id:
                ai_payload = analysis_status_store.get_response_payload(analysis_id)
                ai_status = str(ai_payload.get("status", "unknown") or "unknown")
                if lang == "zh":
                    ai_label_map = {
                        "running": "分析中",
                        "done": "已完成",
                        "canceled": "已取消",
                        "error": "失败",
                    }
                    ai_label = ai_label_map.get(ai_status, ai_status)
                    if ai_status == "running":
                        ai_cell = f'{html.escape(ai_label)} | <a href="{with_lang("/tasks/" + tid + "#ai-analysis", lang)}">查看进度</a>'
                    else:
                        ai_cell = html.escape(ai_label)
                else:
                    ai_label_map = {
                        "running": "Running",
                        "done": "Done",
                        "canceled": "Canceled",
                        "error": "Failed",
                    }
                    ai_label = ai_label_map.get(ai_status, ai_status)
                    if ai_status == "running":
                        ai_cell = f'{html.escape(ai_label)} | <a href="{with_lang("/tasks/" + tid + "#ai-analysis", lang)}">View Progress</a>'
                    else:
                        ai_cell = html.escape(ai_label)
        action_text = "查看" if lang == "zh" else "View"
        delete_text = "删除" if lang == "zh" else "Delete"
        body_rows.append(
            "<tr>"
            f"<td><input type=\"checkbox\" class=\"task-select\" value=\"{html.escape(tid)}\"></td>"
            f"<td><a href=\"{with_lang('/tasks/' + tid, lang)}\">{html.escape(tid)}</a></td>"
            f"<td>{html.escape(_fmt_ts(float(row.get('created_at', 0.0) or 0.0)))}</td>"
            f"<td>{html.escape(str(row.get('status', 'unknown') or 'unknown'))}</td>"
            f"<td>{ai_cell}</td>"
            f"<td>{len(devices)}</td>"
            f"<td>{' | '.join(links)}</td>"
            f"<td><a href=\"{with_lang('/tasks/' + tid, lang)}\">{action_text}</a> | <button type=\"button\" class=\"task-delete\" data-task-id=\"{html.escape(tid)}\">{delete_text}</button></td>"
            "</tr>"
        )
    page_css = (
        build_unified_task_list_css()
        + """
    body { margin:0; background:#f6f8fb; color:#0f172a; font:14px/1.5 "Segoe UI","PingFang SC",sans-serif; }
    .wrap { max-width:980px; margin:28px auto; padding:0 16px; }
    """
    )
    title = "任务页面" if lang == "zh" else "Tasks"
    select_all_text = "全选" if lang == "zh" else "Select All"
    clear_sel_text = "清空选择" if lang == "zh" else "Clear Selection"
    batch_del_text = "删除选中任务" if lang == "zh" else "Delete Selected"
    no_sel_text = "请先选择至少一个任务" if lang == "zh" else "Select at least one task"
    confirm_batch = "确认删除选中的任务及其相关文件吗？" if lang == "zh" else "Delete selected tasks and related files?"
    confirm_single = "确认删除该任务及其相关文件吗？" if lang == "zh" else "Delete this task and related files?"
    deleting_text = "正在删除..." if lang == "zh" else "Deleting..."
    delete_fail = "删除失败: " if lang == "zh" else "Delete failed: "
    body_html = render_html_template(
        "tasks.html",
        {
            "TITLE": html.escape(title),
            "HEAD_0": html.escape(h[0]),
            "HEAD_1": html.escape(h[1]),
            "HEAD_2": html.escape(h[2]),
            "HEAD_3": html.escape(h[3]),
            "HEAD_4": html.escape(h[4]),
            "HEAD_5": html.escape(h[5]),
            "HEAD_6": html.escape(h[6]),
            "HEAD_7": html.escape(h[7]),
            "SELECT_ALL_TEXT": html.escape(select_all_text),
            "CLEAR_SEL_TEXT": html.escape(clear_sel_text),
            "BATCH_DEL_TEXT": html.escape(batch_del_text),
            "TABLE_ROWS": "".join(body_rows) if body_rows else "<tr><td colspan=\"8\">-</td></tr>",
            "LANG_JSON": json.dumps(lang),
            "NO_SEL_TEXT_JSON": json.dumps(no_sel_text),
            "CONFIRM_BATCH_JSON": json.dumps(confirm_batch),
            "CONFIRM_SINGLE_JSON": json.dumps(confirm_single),
            "DELETING_TEXT_JSON": json.dumps(deleting_text),
            "DELETE_FAIL_JSON": json.dumps(delete_fail),
        },
    )
    return render_base_page(
        lang=lang,
        title=title,
        header_html=build_app_header_html(lang, "tasks"),
        page_body=body_html,
        page_css=page_css,
    )
