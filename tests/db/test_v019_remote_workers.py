from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db

EXPECTED_COLUMNS = {
    "worker_id",
    "name",
    "capabilities_json",
    "slots",
    "registered_at",
    "last_seen_at",
}


def test_remote_workers_table_created(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    conn = connect_sqlite(db_path)
    try:
        columns = {row["name"] for row in conn.execute("pragma table_info(remote_workers)")}
        assert columns >= EXPECTED_COLUMNS
        conn.execute(
            "insert into remote_workers"
            " (worker_id, name, capabilities_json, slots, registered_at, last_seen_at)"
            " values ('w1', 'mac-mini', '[\"generate_key_info\"]', 65,"
            " '2026-07-18 00:00:00.000000', '2026-07-18 00:00:00.000000')"
        )
        row = conn.execute("select slots from remote_workers where worker_id = 'w1'").fetchone()
        assert row["slots"] == 65
    finally:
        conn.close()
