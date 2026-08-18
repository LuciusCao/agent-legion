"""Per-run Agent version pins (schema v29): enqueue validation, claim
candidate join, stale-definition sweeps, and the dispatch resolve helper."""

from __future__ import annotations

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.sweepers import fail_stale_definition_requests
from server.app.agent_catalog import AgentDefinition
from server.app.agent_workers import AgentWorkerRegistry
from server.app.db.transaction import read_connection
from server.app.services.agent_service import AgentService
from server.app.services.agent_version_pins import resolve_dispatch_agent_definition
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL

_AGENT = "generator-v1"
_WORKSPACE = "test-workspace"


def _v1() -> AgentDefinition:
    return AgentDefinition(capability="generate", runtime="pi", skill="question/generate")


def _v2() -> AgentDefinition:
    # A draft that differs from v1 in content (hash) and worker requirements.
    return AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )


def _service() -> AgentService:
    return AgentService(TEST_DATABASE_URL, _WORKSPACE)


def _seed_catalog() -> None:
    """v1 published, v2 draft (workspace-scoped catalog, schema v46)."""
    replace_agent_catalog(_WORKSPACE, {_AGENT: _v1()})
    _service().save_draft(_AGENT, _v2(), created_by="test")


def _insert_job_rows(job_db, job_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'test-workspace', 'questions', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'generate')", (job_id,))
        conn.execute(
            "insert into workspace_node_routes("
            "workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values ('test-workspace', 'questions', 'generate', 'agent', %s)"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (_AGENT,),
        )
        conn.execute(
            "insert into workspace_agent_capacities(workspace_id, max_concurrency)"
            " values ('test-workspace', 20) on conflict(workspace_id) do nothing"
        )


def _request(
    job_id: str,
    definition: AgentDefinition,
    *,
    pinned_agent_version: int | None = None,
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        workspace_id="test-workspace",
        job_id=job_id,
        workflow_key="questions",
        node_key="generate",
        agent_id=_AGENT,
        agent_definition_hash=definition.definition_hash(),
        manifest={
            "job_id": job_id,
            "log_path": f"logs/{job_id}.log",
            "execution": {"provider": "gateway", "model": "test-model"},
        },
        pinned_agent_version=pinned_agent_version,
    )


def _request_state(execution_id: str) -> str:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s", (execution_id,)
        ).fetchone()
    return str(row["state"])


def test_enqueue_accepts_pinned_draft_version(job_db) -> None:
    _seed_catalog()
    _insert_job_rows(job_db, "job-pinned")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    execution_id = broker.enqueue(_request("job-pinned", _v2(), pinned_agent_version=2))

    assert execution_id is not None
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select pinned_agent_version from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row["pinned_agent_version"] == 2


def test_enqueue_rejects_pin_mismatch(job_db) -> None:
    _seed_catalog()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    _insert_job_rows(job_db, "job-hash")
    with pytest.raises(ValueError, match="pinned Agent version"):
        # Pin says v2 but the hash is v1's.
        broker.enqueue(_request("job-hash", _v1(), pinned_agent_version=2))
    _insert_job_rows(job_db, "job-version")
    with pytest.raises(ValueError, match="pinned Agent version"):
        broker.enqueue(_request("job-version", _v2(), pinned_agent_version=99))


def test_enqueue_unpinned_still_requires_published(job_db) -> None:
    _seed_catalog()
    _insert_job_rows(job_db, "job-draft")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    with pytest.raises(ValueError, match="unavailable or changed"):
        broker.enqueue(_request("job-draft", _v2()))
    assert broker.enqueue(_request("job-draft", _v1())) is not None


def test_claim_joins_the_pinned_version(job_db) -> None:
    """The claim candidate join must resolve the pinned draft, not published.

    v2 requires a worker label the registered worker lacks: if the join
    wrongly selected the published v1 definition, the pinned request would be
    claimable. The unpinned v1 request on another job must claim normally.
    """
    _seed_catalog()
    _insert_job_rows(job_db, "job-pinned")
    _insert_job_rows(job_db, "job-published")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert broker.enqueue(_request("job-pinned", _v2(), pinned_agent_version=2)) is not None
    assert broker.enqueue(_request("job-published", _v1())) is not None
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={},
        capabilities=["generate"],
        models=[{"provider": "gateway", "model": "test-model"}],
    )

    claimed = broker.claim("worker-1")

    assert claimed is not None
    assert claimed.job_id == "job-published"


def test_stale_definition_sweeper_respects_pin(job_db) -> None:
    _seed_catalog()
    _insert_job_rows(job_db, "job-unpinned")
    _insert_job_rows(job_db, "job-pinned")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    unpinned_id = broker.enqueue(_request("job-unpinned", _v1()))
    pinned_id = broker.enqueue(_request("job-pinned", _v2(), pinned_agent_version=2))
    assert unpinned_id is not None and pinned_id is not None

    # Publishing v2 archives v1: the unpinned request goes stale, the pinned
    # one stays valid because its immutable version row still exists.
    _service().publish(_AGENT)
    failed = fail_stale_definition_requests(broker)

    assert failed == [unpinned_id]
    assert _request_state(unpinned_id) == "done"
    assert _request_state(pinned_id) == "queued"


def test_resolve_dispatch_agent_definition() -> None:
    _seed_catalog()
    assert resolve_dispatch_agent_definition(TEST_DATABASE_URL, _WORKSPACE, _AGENT, None) == _v1()

    pin = {"agent_id": _AGENT, "version": 2, "definition_hash": _v2().definition_hash()}
    assert resolve_dispatch_agent_definition(TEST_DATABASE_URL, _WORKSPACE, _AGENT, pin) == _v2()

    # No global fallback (schema v46): the same agent id is invisible in
    # another workspace.
    other = "other-workspace"
    assert resolve_dispatch_agent_definition(TEST_DATABASE_URL, other, _AGENT, None) is None

    with pytest.raises(ValueError, match="routes to"):
        resolve_dispatch_agent_definition(
            TEST_DATABASE_URL, _WORKSPACE, _AGENT, {**pin, "agent_id": "other"}
        )
    with pytest.raises(ValueError, match="does not exist"):
        resolve_dispatch_agent_definition(
            TEST_DATABASE_URL, _WORKSPACE, _AGENT, {**pin, "version": 99}
        )
    with pytest.raises(ValueError, match="hash mismatch"):
        resolve_dispatch_agent_definition(
            TEST_DATABASE_URL,
            _WORKSPACE,
            _AGENT,
            {**pin, "definition_hash": _v1().definition_hash()},
        )
