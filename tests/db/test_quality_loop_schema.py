"""Schema v28/v29: quality loop sampling/label tables and replay support."""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_quality_tables_exist() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        tables = {
            row["tablename"]
            for row in conn.execute(
                "select tablename from pg_tables where schemaname=current_schema()"
            ).fetchall()
        }
        indexes = {
            row["indexname"]
            for row in conn.execute(
                "select indexname from pg_indexes where schemaname=current_schema()"
            ).fetchall()
        }
    assert {
        "quality_sample_batches",
        "quality_sample_items",
        "quality_labels",
        "quality_replays",
    } <= tables
    assert "idx_quality_labels_item_target" in indexes
    assert "idx_quality_sample_items_batch" in indexes
    assert "idx_quality_replays_item" in indexes
    assert "quality_replays_one_active_per_item" in indexes


def test_schema_v29_columns_exist() -> None:
    """v29: quality_labels.replay_id and agent_execution_requests pins."""
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select table_name, column_name from information_schema.columns"
            " where table_schema=current_schema()"
            " and table_name in ('quality_labels', 'agent_execution_requests')"
        ).fetchall()
    columns = {(row["table_name"], row["column_name"]) for row in rows}
    assert ("quality_labels", "replay_id") in columns
    assert ("agent_execution_requests", "pinned_agent_version") in columns


def test_quality_label_constraints_enforced() -> None:
    import psycopg
    import pytest

    from server.app.db.transaction import write_transaction

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws-ql', 'ws-ql', 'question_comprehension_info') on conflict do nothing"
        )
        conn.execute(
            "insert into quality_sample_batches(id, workspace_id, sample_size)"
            " values ('b-ql', 'ws-ql', 1)"
        )
        conn.execute(
            "insert into quality_sample_items(id, batch_id, node_run_id, job_id)"
            " values ('i-ql', 'b-ql', 1, 'j-ql')"
        )
        conn.execute(
            "insert into quality_labels(id, item_id, target, verdict)"
            " values ('l-ql', 'i-ql', 'run', 'maybe')"
        )
