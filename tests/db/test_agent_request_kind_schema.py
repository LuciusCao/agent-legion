"""Schema v38: agent_execution_requests.kind + agent_workers.max_code_concurrency."""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _insert_agent_job(conn, job_id: str = "job-kind-probe") -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'question_comprehension_info')"
        " on conflict(id) do nothing"
    )
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
        " values (%s, 'test-workspace', 'questions', 'question', %s)",
        (job_id, job_id),
    )


def _insert_request(conn, kind: str | None) -> None:
    columns = (
        "execution_id, workspace_id, job_id, workflow_key, node_key,"
        " agent_id, agent_definition_hash, node_concurrency_limit,"
        " queued_at, manifest_json"
    )
    if kind is None:
        conn.execute(
            f"insert into agent_execution_requests({columns})"
            " values ('exec-kind-default', 'test-workspace', 'job-kind-probe',"
            " 'questions', 'package', 'package', 'hash', 1, current_timestamp, '{}')",
        )
        return
    conn.execute(
        f"insert into agent_execution_requests({columns}, kind)"
        " values ('exec-kind-explicit', 'test-workspace', 'job-kind-probe',"
        " 'questions', 'package', 'package', 'hash', 1, current_timestamp, '{}', %s)",
        (kind,),
    )


def test_request_kind_defaults_to_agent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_agent_job(conn)
        _insert_request(conn, None)
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select kind from agent_execution_requests where execution_id='exec-kind-default'"
        ).fetchone()
    assert row["kind"] == "agent"


def test_request_kind_check_rejects_unknown_values() -> None:
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        _insert_agent_job(conn)
        _insert_request(conn, "bogus")


def test_worker_code_capacity_defaults_to_zero_and_rejects_negative() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_workers("
            "worker_id, runtimes_json, max_concurrency, protocol_version, token_hash,"
            " registered_at, last_seen_at)"
            " values ('w-kind', '[\"pi\"]', 1, 1, 'hash', current_timestamp, current_timestamp)"
        )
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select max_code_concurrency from agent_workers where worker_id='w-kind'"
        ).fetchone()
    assert row["max_code_concurrency"] == 0

    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_workers("
            "worker_id, runtimes_json, max_concurrency, max_code_concurrency,"
            " protocol_version, token_hash, registered_at, last_seen_at)"
            " values ('w-negative', '[\"pi\"]', 1, -1, 1, 'hash',"
            " current_timestamp, current_timestamp)"
        )


@pytest.mark.fresh_schema
def test_schema_rebuild_keeps_kind_columns() -> None:
    """The fresh_schema rebuild re-applies the full DDL; both v38 columns and
    their constraints must survive the replay (idempotent alter statements)."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_agent_job(conn)
        _insert_request(conn, "code")
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select kind from agent_execution_requests where execution_id='exec-kind-explicit'"
        ).fetchone()
    assert row["kind"] == "code"
