"""Batched active-request lookup for the workflow worker's poll pass."""

from __future__ import annotations

from server.app.agent_broker import batch
from server.app.agent_broker.batch import active_request_keys
from tests.postgres_support import TEST_DATABASE_URL


def _seed_job(job_db, job_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'Test', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'ws1', 'questions', 'question', %s)",
            (job_id, job_id),
        )


def _insert_request(job_db, *, job_id: str, node_key: str, state: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id,"
            " workflow_key, node_key, agent_id, agent_definition_hash,"
            " node_concurrency_limit, queued_at, manifest_json, state)"
            " values (%s, 'ws1', %s, 'questions', %s, 'agent-x', 'hash', 1,"
            " current_timestamp, '{}', %s)",
            (f"{job_id}-{node_key}-{state}", job_id, node_key, state),
        )


def test_empty_job_ids_returns_empty_set(job_db) -> None:
    assert active_request_keys(TEST_DATABASE_URL, []) == set()


def test_active_states_returned_terminal_states_excluded(job_db) -> None:
    for state in ("queued", "claimed", "reporting", "done", "cancelled"):
        _seed_job(job_db, f"job-{state}")
        _insert_request(job_db, job_id=f"job-{state}", node_key="n1", state=state)

    keys = active_request_keys(
        TEST_DATABASE_URL,
        ["job-queued", "job-claimed", "job-reporting", "job-done", "job-cancelled", "job-none"],
    )

    assert keys == {("job-queued", "n1"), ("job-claimed", "n1"), ("job-reporting", "n1")}


def test_chunked_query_aggregates_all_chunks(job_db, monkeypatch) -> None:
    monkeypatch.setattr(batch, "_CHUNK_SIZE", 1)
    expected = set()
    for index in range(3):
        job_id = f"job-{index}"
        _seed_job(job_db, job_id)
        _insert_request(job_db, job_id=job_id, node_key=f"n{index}", state="queued")
        expected.add((job_id, f"n{index}"))

    assert active_request_keys(TEST_DATABASE_URL, ["job-0", "job-1", "job-2"]) == expected
