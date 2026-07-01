from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    subject_id INTEGER,
    question_uuid TEXT,
    question_vno INTEGER,
    format_vno TEXT,
    comprehension_difficulty INTEGER,
    comprehension_data_hash TEXT,
    action TEXT,
    status TEXT,
    api_code INTEGER,
    api_message TEXT,
    api_response TEXT,
    uploaded_record_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_question ON upload_logs(question_id);
CREATE INDEX IF NOT EXISTS idx_logs_fingerprint ON upload_logs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_logs_batch ON upload_logs(batch_id);

CREATE TABLE IF NOT EXISTS question_state (
    question_id TEXT PRIMARY KEY,
    latest_fingerprint TEXT NOT NULL,
    latest_upload_log_id INTEGER REFERENCES upload_logs(id),
    last_scan_at TEXT,
    stale_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    old_fingerprint TEXT NOT NULL,
    new_fingerprint TEXT NOT NULL,
    detected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_question ON scan_results(question_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self.logs = LogStore(self._conn)
        self.states = QuestionStateStore(self._conn)
        self.scan_results = ScanResultStore(self._conn)

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class LogStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        batch_id: str,
        question_id: str,
        fingerprint: str,
        subject_id: int | None = None,
        question_uuid: str | None = None,
        question_vno: int | None = None,
        format_vno: str | None = None,
        comprehension_difficulty: int | None = None,
        comprehension_data_hash: str | None = None,
        action: str | None = None,
        status: str | None = None,
        api_code: int | None = None,
        api_message: str | None = None,
        api_response: str | None = None,
        uploaded_record_id: int | None = None,
    ) -> int:
        now = _now()
        cursor = self._conn.execute(
            """
            INSERT INTO upload_logs (
                batch_id, question_id, fingerprint, subject_id, question_uuid,
                question_vno, format_vno, comprehension_difficulty, comprehension_data_hash,
                action, status, api_code, api_message, api_response, uploaded_record_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                question_id,
                fingerprint,
                subject_id,
                question_uuid,
                question_vno,
                format_vno,
                comprehension_difficulty,
                comprehension_data_hash,
                action,
                status,
                api_code,
                api_message,
                api_response,
                uploaded_record_id,
                now,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_latest_success(self, question_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT * FROM upload_logs
            WHERE question_id = ? AND status = 'success'
            ORDER BY id DESC LIMIT 1
            """,
            (question_id,),
        ).fetchone()

    def get_logs(self, question_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM upload_logs WHERE question_id = ? ORDER BY id DESC",
            (question_id,),
        ).fetchall()


class QuestionStateStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_state(
        self,
        question_id: str,
        fingerprint: str,
        upload_log_id: int | None,
    ) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT INTO question_state (
                question_id, latest_fingerprint, latest_upload_log_id, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                latest_fingerprint = excluded.latest_fingerprint,
                latest_upload_log_id = excluded.latest_upload_log_id,
                updated_at = excluded.updated_at
            """,
            (question_id, fingerprint, upload_log_id, now),
        )
        self._conn.commit()

    def update_scan(
        self,
        question_id: str,
        scan_at: str,
        stale_reason: str | None = None,
    ) -> None:
        now = _now()
        self._conn.execute(
            """
            UPDATE question_state
            SET last_scan_at = ?, stale_reason = ?, updated_at = ?
            WHERE question_id = ?
            """,
            (scan_at, stale_reason, now, question_id),
        )
        self._conn.commit()

    def get(self, question_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM question_state WHERE question_id = ?",
            (question_id,),
        ).fetchone()

    def get_all(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM question_state").fetchall()


class ScanResultStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(
        self,
        question_id: str,
        old_fingerprint: str,
        new_fingerprint: str,
        detected_at: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO scan_results (question_id, old_fingerprint, new_fingerprint, detected_at)
            VALUES (?, ?, ?, ?)
            """,
            (question_id, old_fingerprint, new_fingerprint, detected_at),
        )
        self._conn.commit()
        return cursor.lastrowid or 0
