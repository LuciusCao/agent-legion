from __future__ import annotations

from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def test_v022_creates_seq_and_control_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    conn = connect_sqlite(db_path)
    try:
        assert conn.execute("select value from job_event_seq where id = 1").fetchone()["value"] == 0
        version = conn.execute("select sqlite_version() as v").fetchone()["v"]
        major, minor, *_ = (int(p) for p in version.split("."))
        assert (major, minor) >= (3, 35), "RETURNING requires sqlite >= 3.35"
        cols = {row["name"] for row in conn.execute("pragma table_info(worker_control_state)")}
        assert {"scope", "paused", "updated_by", "updated_at"} <= cols
    finally:
        conn.close()
