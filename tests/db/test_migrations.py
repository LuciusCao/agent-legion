from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def test_v018_creates_node_run_token_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with closing(connect_sqlite(db_path)) as conn:
        row = conn.execute(
            "select 1 from sqlite_master where type='table' and name='node_run_token_usage'"
        ).fetchone()
        assert row is not None
        indexes = {
            r["name"]
            for r in conn.execute(
                "select name from sqlite_master where type='index' and tbl_name='node_run_token_usage'"
            ).fetchall()
        }
        assert "idx_node_run_token_usage_workspace" in indexes
        assert "idx_node_run_token_usage_model" in indexes
        assert "idx_node_run_token_usage_skill_version" in indexes
