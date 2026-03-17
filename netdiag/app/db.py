from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import TaskRecord


class TaskDB:
    def __init__(self, db_path: str = "./tasks.db") -> None:
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_total INTEGER NOT NULL,
                    progress_done INTEGER NOT NULL,
                    params_json TEXT NOT NULL,
                    devices_json TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def upsert_task(self, task: TaskRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks(task_id, created_at, updated_at, status, progress_total, progress_done, params_json, devices_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    progress_total=excluded.progress_total,
                    progress_done=excluded.progress_done,
                    params_json=excluded.params_json,
                    devices_json=excluded.devices_json
                """,
                (
                    task.task_id,
                    task.created_at,
                    task.updated_at,
                    task.status,
                    task.progress_total,
                    task.progress_done,
                    json.dumps(task.params, ensure_ascii=False),
                    json.dumps([d.model_dump() for d in task.devices], ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return TaskRecord(
            task_id=row["task_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=row["status"],
            progress_total=row["progress_total"],
            progress_done=row["progress_done"],
            params=json.loads(row["params_json"]),
            devices=json.loads(row["devices_json"]),
        )

    def list_tasks(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [
            TaskRecord(
                task_id=row["task_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                status=row["status"],
                progress_total=row["progress_total"],
                progress_done=row["progress_done"],
                params=json.loads(row["params_json"]),
                devices=json.loads(row["devices_json"]),
            )
            for row in rows
        ]

    def delete_tasks(self, task_ids: list[str]) -> int:
        ids = [str(t or "").strip() for t in (task_ids or []) if str(t or "").strip()]
        if not ids:
            return 0
        placeholders = ",".join(["?"] * len(ids))
        with self._lock:
            cur = self._conn.execute(f"DELETE FROM tasks WHERE task_id IN ({placeholders})", ids)
            self._conn.commit()
            return int(cur.rowcount or 0)

    def delete_tasks_exact(self, task_ids: list[str]) -> list[str]:
        ids = [str(t or "").strip() for t in (task_ids or []) if str(t or "").strip()]
        if not ids:
            return []
        deleted: list[str] = []
        with self._lock:
            for tid in ids:
                cur = self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (tid,))
                if int(cur.rowcount or 0) > 0:
                    deleted.append(tid)
            self._conn.commit()
        return deleted
