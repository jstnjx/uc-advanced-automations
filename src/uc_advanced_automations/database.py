# Advanced Automations v2.0.0
"""SQLite-backed run history, logs, and automation revisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AutomationDatabase:
    """Persist execution history and revision snapshots in one local database."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            self.path: Path | None = None
            connection_target = ":memory:"
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            self.path = data_dir / "automation-data.sqlite3"
            connection_target = str(self.path)
        self._lock = RLock()
        self._conn = sqlite3.connect(connection_target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL,
                    automation_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms REAL,
                    current_step TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runs_automation_started
                    ON runs(automation_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_status
                    ON runs(status);

                CREATE TABLE IF NOT EXISTS run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    automation_id TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_automation
                    ON run_events(automation_id, sequence DESC);

                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    automation_id TEXT NOT NULL,
                    automation_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    action TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_revisions_automation
                    ON revisions(automation_id, revision_id DESC);
                """
            )
            now = utc_now()
            self._conn.execute(
                """UPDATE runs
                   SET status='cancelled', finished_at=?, error=COALESCE(error, 'Service restarted'),
                       duration_ms=(julianday(?) - julianday(started_at)) * 86400000.0,
                       current_step=NULL
                   WHERE status='running'""",
                (now, now),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def start_run(
        self,
        run_id: str,
        automation_id: str,
        automation_name: str,
        source: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, automation_id, automation_name, source, status, started_at,
                    finished_at, duration_ms, current_step, error)
                   VALUES (?, ?, ?, ?, 'running', ?, NULL, NULL, NULL, NULL)""",
                (run_id, automation_id, automation_name, source, utc_now()),
            )
            self._conn.commit()

    def set_current_step(self, run_id: str, step: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET current_step=? WHERE run_id=? AND status='running'",
                (step, run_id),
            )
            self._conn.commit()

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """UPDATE runs
                   SET status=?, finished_at=?,
                       duration_ms=(julianday(?) - julianday(started_at)) * 86400000.0,
                       current_step=NULL, error=?
                   WHERE run_id=?""",
                (status, now, now, error, run_id),
            )
            self._conn.commit()

    def log_event(
        self,
        level: str,
        run_id: str,
        automation_id: str,
        message: str,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO run_events(timestamp, level, run_id, automation_id, message)
                   VALUES (?, ?, ?, ?, ?)""",
                (datetime.now().astimezone().isoformat(timespec="seconds"), level, run_id, automation_id, message),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def logs_after(self, sequence: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT sequence, timestamp, level, run_id, automation_id, message
                   FROM run_events WHERE sequence > ? ORDER BY sequence ASC LIMIT ?""",
                (sequence, max(1, min(limit, 5000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_triggered_name(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT automation_name FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return str(row[0]) if row else None

    def run_summary(self, automation_id: str, recent_limit: int = 10) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT run_id, automation_id, automation_name, source, status, started_at,
                          finished_at, duration_ms, current_step, error
                   FROM runs WHERE automation_id=? ORDER BY started_at DESC LIMIT ?""",
                (automation_id, max(10, recent_limit)),
            ).fetchall()
            last_success = self._conn.execute(
                """SELECT started_at, finished_at, duration_ms, status FROM runs
                   WHERE automation_id=? AND status IN ('success','stopped')
                   ORDER BY started_at DESC LIMIT 1""",
                (automation_id,),
            ).fetchone()
            last_failure = self._conn.execute(
                """SELECT started_at, finished_at, duration_ms, status, error FROM runs
                   WHERE automation_id=? AND status='failure'
                   ORDER BY started_at DESC LIMIT 1""",
                (automation_id,),
            ).fetchone()
            average = self._conn.execute(
                """SELECT AVG(duration_ms) FROM runs
                   WHERE automation_id=? AND duration_ms IS NOT NULL""",
                (automation_id,),
            ).fetchone()[0]
        recent = [dict(row) for row in rows[:recent_limit]]
        active = next((item for item in recent if item["status"] == "running"), None)
        return {
            "last_run": recent[0] if recent else None,
            "last_successful_run": dict(last_success) if last_success else None,
            "last_failure": dict(last_failure) if last_failure else None,
            "average_duration_ms": float(average) if average is not None else None,
            "recent_runs": recent,
            "currently_active_step": active.get("current_step") if active else None,
            "active_run_id": active.get("run_id") if active else None,
        }

    def run_summaries(self, automation_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {automation_id: self.run_summary(automation_id, recent_limit=5) for automation_id in automation_ids}

    def record_revision(
        self,
        automation: dict[str, Any],
        *,
        source: str,
        action: str,
    ) -> int:
        automation_id = str(automation.get("id", ""))
        if not automation_id:
            raise ValueError("Revision snapshot requires an automation id")
        payload = json.dumps(automation, ensure_ascii=False, sort_keys=True)
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO revisions
                   (automation_id, automation_name, created_at, source, action, snapshot_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    automation_id,
                    str(automation.get("name") or "Automation"),
                    utc_now(),
                    source,
                    action,
                    payload,
                ),
            )
            revision_id = int(cursor.lastrowid)
            self._conn.execute(
                """DELETE FROM revisions WHERE revision_id IN (
                       SELECT revision_id FROM revisions WHERE automation_id=?
                       ORDER BY revision_id DESC LIMIT -1 OFFSET 50
                   )""",
                (automation_id,),
            )
            self._conn.commit()
            return revision_id

    def list_revisions(self, automation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT revision_id, automation_id, automation_name, created_at, source, action
                   FROM revisions WHERE automation_id=? ORDER BY revision_id DESC LIMIT 50""",
                (automation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_revision(self, revision_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT revision_id, automation_id, automation_name, created_at, source,
                          action, snapshot_json
                   FROM revisions WHERE revision_id=?""",
                (revision_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["automation"] = json.loads(result.pop("snapshot_json"))
        return result

    def list_deleted_automations(self, active_ids: list[str]) -> list[dict[str, Any]]:
        """Return the newest delete snapshot for every automation that is still absent."""

        active = set(active_ids)
        with self._lock:
            rows = self._conn.execute(
                """SELECT revision_id, automation_id, automation_name, created_at, source, action
                   FROM revisions WHERE action='delete' ORDER BY revision_id DESC"""
            ).fetchall()
        deleted: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            item = dict(row)
            automation_id = str(item["automation_id"])
            if automation_id in active or automation_id in seen:
                continue
            seen.add(automation_id)
            deleted.append(item)
        return deleted
