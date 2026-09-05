"""Schema v79 (#401): shard-aware one-active-request index.

``idx_agent_requests_one_active_node`` dedups active requests per execution
identity: non-shard rows stay one-active per (job_id, node_key) — the manifest
without a top-level ``shard_index`` collapses to the COALESCE fallback -1
(load-bearing: a bare NULL never participates in Postgres unique dedup) —
while remote shard rows (#389) dedup per (job_id, node_key, shard_index),
matching the ``node_shards`` row-level binding ``try_start_shard`` performs
at claim time. This file pins: the index shape, the NULL semantics, the
per-shard parallel enqueue/claim (the issue's serialization gap), the
unchanged ordinary-node single-active behavior, and the v78 → v79 upgrade
path.
"""

from __future__ import annotations

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _broker(data_dir) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir)


def _insert_job_rows(job_db, *, job_id: str, node_key: str = "package") -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('test-workspace', 'Test', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, 'test-workspace', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def _code_request(
    job_id: str, node_key: str = "package", shard_index: int | None = None
) -> AgentExecutionRequest:
    manifest: dict = {
        "kind": "code",
        "workspace_id": "test-workspace",
        "capability": "package",
        "code_hash": "abc123",
        "job_id": job_id,
        "log_path": f"logs/{job_id}.log",
        "config": {"mode": "fast"},
    }
    if shard_index is not None:
        manifest["shard_index"] = shard_index
        manifest["shard_input"] = {"q": shard_index}
    return AgentExecutionRequest(
        workspace_id="test-workspace",
        job_id=job_id,
        workflow_key="questions",
        node_key=node_key,
        agent_id="package",
        agent_definition_hash="codehash",
        manifest=manifest,
        kind="code",
    )


def _materialize_shards(job_db, job_id: str, node_key: str, count: int) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.executemany(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values (%s, %s, %s, 'pending', '{}') on conflict do nothing",
            [(job_id, node_key, index) for index in range(count)],
        )


def _register_code_worker(*, max_code_concurrency: int = 10) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-shard-idx",
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        max_code_concurrency=max_code_concurrency,
        protocol_version=2,
    )


def _index_definition(conn) -> str:
    row = conn.execute(
        "select indexdef from pg_indexes where schemaname=current_schema()"
        " and indexname='idx_agent_requests_one_active_node'"
    ).fetchone()
    return str(row["indexdef"]) if row is not None else ""


def _is_shard_aware(definition: str) -> bool:
    """pg_get_indexdef normalizes the expression (extra ::text/::jsonb casts,
    quoted '-1'), so compare the normalized form it emits."""
    return (
        "COALESCE((((manifest_json)::jsonb ->> 'shard_index'::text))::integer, '-1'::integer)"
        in definition
    )


# ---------------------------------------------------------------------------
# Schema pin
# ---------------------------------------------------------------------------


def test_schema_version_pin() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=%s", (79,)).fetchone()
    assert row is not None
    assert row["name"] == "shard_identity_index"


def test_index_definition_is_shard_aware() -> None:
    """The unique key carries the shard identity expression; the partial
    predicate (queued/claimed/reporting) is unchanged."""
    with read_connection(TEST_DATABASE_URL) as conn:
        definition = _index_definition(conn)
    assert "UNIQUE INDEX" in definition.upper()
    assert _is_shard_aware(definition)
    assert "state = ANY" in definition
    for state in ("queued", "claimed", "reporting"):
        assert f"'{state}'" in definition
    # The retired two-column shape must be gone.
    assert "USING btree (job_id, node_key) WHERE" not in definition


def test_migration_recorded_after_upgrade_from_v78(job_db) -> None:
    """A database recorded at v78 must gain the shard-aware index via the
    v79 migration: the schema-file replay recreates the NEW shape (drop +
    create under the same name), and the migration itself must be recorded."""
    _insert_job_rows(job_db, job_id="job-v78-upgrade")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 79")

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        record = conn.execute(
            "select name from schema_migrations where version=%s", (79,)
        ).fetchone()
        definition = _index_definition(conn)
    assert record is not None and record["name"] == "shard_identity_index"
    assert _is_shard_aware(definition)


@pytest.mark.fresh_schema
def test_upgrade_replaces_two_column_index_shape(job_db) -> None:
    """Rewind to the pre-v79 two-column index and upgrade: init_db's replay
    must replace it with the shard-aware expression index (drop + create,
    not create-if-not-exists leaving the old shape)."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("drop index if exists idx_agent_requests_one_active_node")
        conn.execute(
            "create unique index idx_agent_requests_one_active_node"
            " on agent_execution_requests(job_id, node_key)"
            " where state in ('queued', 'claimed', 'reporting')"
        )
        conn.execute("delete from schema_migrations where version >= 79")
        assert not _is_shard_aware(_index_definition(conn))  # old shape confirmed

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        definition = _index_definition(conn)
    assert _is_shard_aware(definition)


# ---------------------------------------------------------------------------
# Index semantics: NULL handling + per-shard dedup
# ---------------------------------------------------------------------------


def test_non_shard_rows_stay_single_active(job_db) -> None:
    """A manifest WITHOUT shard_index must keep the old one-active-per-node
    semantics: the COALESCE fallback -1 participates in the unique check (a
    bare NULL expression would let unlimited rows through)."""
    from psycopg import IntegrityError

    _insert_job_rows(job_db, job_id="job-null-sem")
    broker = _broker(job_db.jobs_dir.parent)
    assert broker.enqueue(_code_request("job-null-sem")) is not None
    # A second non-shard row for the same (job, node) — different manifest
    # content, no shard_index — must hit the unique index.
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key, kind, agent_id,"
            " agent_definition_hash, node_concurrency_limit, queued_at, manifest_json)"
            " values ('exec-null-2', 'test-workspace', 'job-null-sem', 'package', 'code',"
            " 'package', 'codehash', 1, current_timestamp, '{}')"
        )


def test_terminal_states_free_the_identity(job_db) -> None:
    """done/cancelled rows leave the partial index; the next request for the
    same identity enqueues (the predicate only covers queued/claimed/
    reporting — unchanged by v79)."""
    _insert_job_rows(job_db, job_id="job-terminal")
    broker = _broker(job_db.jobs_dir.parent)
    first = broker.enqueue(_code_request("job-terminal", shard_index=2))
    assert first is not None
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update agent_execution_requests set state='done', finished_at=current_timestamp"
            " where execution_id=%s",
            (first,),
        )
    assert broker.enqueue(_code_request("job-terminal", shard_index=2)) is not None


def test_shard_and_non_shard_rows_coexist(job_db) -> None:
    """Shard 0 (identity 0) and a non-shard row (identity -1) are distinct
    execution identities of the same node — both may be active at once."""
    _insert_job_rows(job_db, job_id="job-coexist")
    broker = _broker(job_db.jobs_dir.parent)
    assert broker.enqueue(_code_request("job-coexist")) is not None  # non-shard
    assert broker.enqueue(_code_request("job-coexist", shard_index=0)) is not None


def test_duplicate_shard_row_rejected_at_index_level(job_db) -> None:
    from psycopg import IntegrityError

    _insert_job_rows(job_db, job_id="job-dup-shard")
    broker = _broker(job_db.jobs_dir.parent)
    assert broker.enqueue(_code_request("job-dup-shard", shard_index=3)) is not None
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key, kind, agent_id,"
            " agent_definition_hash, node_concurrency_limit, queued_at, manifest_json)"
            " values ('exec-dup-shard', 'test-workspace', 'job-dup-shard', 'package', 'code',"
            " 'package', 'codehash', 1, current_timestamp,"
            ' \'{"shard_index": 3, "shard_input": {}}\')'
        )


# ---------------------------------------------------------------------------
# Claim-side gate: has_active_request mirrors the index identity
# ---------------------------------------------------------------------------


def test_has_active_request_gate_is_shard_scoped(job_db) -> None:
    """The gate mirrors the index: a shard query ignores other shards'
    active rows (the #401 serialization gap); a non-shard query still sees
    every active row of the node."""
    _insert_job_rows(job_db, job_id="job-gate")
    broker = _broker(job_db.jobs_dir.parent)
    assert broker.enqueue(_code_request("job-gate", shard_index=0)) is not None
    assert broker.enqueue(_code_request("job-gate", shard_index=1)) is not None

    # Shard-scoped: other shards' rows do not block.
    assert broker.has_active_request("job-gate", "package", shard_index=0) is True
    assert broker.has_active_request("job-gate", "package", shard_index=1) is True
    assert broker.has_active_request("job-gate", "package", shard_index=2) is False
    # Node-level (non-shard): sees every active row — unchanged semantics.
    assert broker.has_active_request("job-gate", "package") is True


def test_parallel_shard_claims_bind_distinct_node_shards(job_db) -> None:
    """End-to-end over the broker: N queued shard requests of one node are
    claimed by a fleet-capable Worker in back-to-back claim calls — the
    pre-v79 single-active index let only one shard exist at a time, so this
    exact sequence was impossible (the second enqueue returned None)."""
    _register_code_worker(max_code_concurrency=10)
    _insert_job_rows(job_db, job_id="job-parallel")
    _materialize_shards(job_db, "job-parallel", "package", 4)
    broker = _broker(job_db.jobs_dir.parent)
    execution_ids = [
        broker.enqueue(_code_request("job-parallel", shard_index=index)) for index in range(4)
    ]
    assert all(execution_id is not None for execution_id in execution_ids)

    claims = [broker.claim("worker-shard-idx") for _ in range(4)]
    assert all(claim is not None for claim in claims), "shard claims must not serialize"
    assert len({claim.execution_id for claim in claims if claim is not None}) == 4

    # Each claim bound its own node_shards row (try_start_shard dedup).
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select shard_index, status, execution_id from node_shards"
            " where job_id='job-parallel' and node_key='package' order by shard_index"
        ).fetchall()
    assert [int(row["shard_index"]) for row in rows] == [0, 1, 2, 3]
    assert all(row["status"] == "running" for row in rows)
    assert {str(row["execution_id"]) for row in rows} == set(execution_ids)


def test_reporting_shard_still_blocks_its_own_identity(job_db) -> None:
    """'reporting' keeps owning the identity until the result commits: a
    re-enqueue of the SAME shard is rejected while other shards proceed."""
    _register_code_worker()
    _insert_job_rows(job_db, job_id="job-reporting")
    _materialize_shards(job_db, "job-reporting", "package", 2)
    broker = _broker(job_db.jobs_dir.parent)
    first = broker.enqueue(_code_request("job-reporting", shard_index=0))
    assert first is not None
    second = broker.enqueue(_code_request("job-reporting", shard_index=1))
    assert second is not None

    claimed = broker.claim("worker-shard-idx")
    assert claimed is not None and claimed.execution_id == first
    assert broker.release_slot(claimed.execution_id, "worker-shard-idx", claimed.lease_id) is True

    assert broker.has_active_request("job-reporting", "package", shard_index=0) is True
    assert broker.enqueue(_code_request("job-reporting", shard_index=0)) is None
    # The sibling shard is untouched by shard 0's reporting state.
    assert broker.has_active_request("job-reporting", "package", shard_index=1) is True
    assert broker.claim("worker-shard-idx") is not None
