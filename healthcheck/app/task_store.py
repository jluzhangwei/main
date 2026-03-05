#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DB_PATH = PROJECT_ROOT / "state" / "tasks.db"


class TaskStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path.as_posix(), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    username TEXT,
                    devices_json TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    report_json TEXT,
                    report_csv TEXT,
                    output_text TEXT
                )
                """
            )
            self._conn.commit()

    def create_task(
        self,
        task_id: str,
        *,
        username: str,
        devices: List[str],
        checks: List[str],
        options: Dict,
    ) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks(
                    task_id, created_at, updated_at, status, exit_code,
                    username, devices_json, checks_json, options_json,
                    report_json, report_csv, output_text
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    exit_code=excluded.exit_code,
                    username=excluded.username,
                    devices_json=excluded.devices_json,
                    checks_json=excluded.checks_json,
                    options_json=excluded.options_json,
                    report_json=excluded.report_json,
                    report_csv=excluded.report_csv,
                    output_text=excluded.output_text
                """,
                (
                    task_id,
                    now,
                    now,
                    "running",
                    None,
                    username,
                    json.dumps(devices, ensure_ascii=False),
                    json.dumps(checks, ensure_ascii=False),
                    json.dumps(options or {}, ensure_ascii=False),
                    "",
                    "",
                    "",
                ),
            )
            self._conn.commit()

    def update_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        exit_code: Optional[int] = None,
        report_json: Optional[str] = None,
        report_csv: Optional[str] = None,
        output_text: Optional[str] = None,
    ) -> None:
        fields = ["updated_at = ?"]
        values: List = [time.time()]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if exit_code is not None:
            fields.append("exit_code = ?")
            values.append(int(exit_code))
        if report_json is not None:
            fields.append("report_json = ?")
            values.append(str(report_json))
        if report_csv is not None:
            fields.append("report_csv = ?")
            values.append(str(report_csv))
        if output_text is not None:
            fields.append("output_text = ?")
            values.append(str(output_text))
        values.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?"
        with self._lock:
            self._conn.execute(sql, values)
            self._conn.commit()

    def get_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_tasks(self, limit: int = 200) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        def _loads(value: str, fallback):
            try:
                return json.loads(value or "")
            except Exception:
                return fallback

        return {
            "task_id": str(row["task_id"]),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
            "status": str(row["status"] or "unknown"),
            "exit_code": row["exit_code"],
            "username": str(row["username"] or ""),
            "devices": _loads(row["devices_json"], []),
            "checks": _loads(row["checks_json"], []),
            "options": _loads(row["options_json"], {}),
            "report_json": str(row["report_json"] or ""),
            "report_csv": str(row["report_csv"] or ""),
            "output_text": str(row["output_text"] or ""),
        }

