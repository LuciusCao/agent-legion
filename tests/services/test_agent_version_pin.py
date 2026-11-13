"""Per-run Agent version pins (schema v29): enqueue validation, claim
candidate join, stale-definition sweeps, and the dispatch resolve helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.sweeper_definitions import fail_stale_definition_requests
from server.app.agent_broker.unclaimable import fail_unclaimable_model_requests
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import read_connection
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.agent_node_profile_catalog import (
    fresh_legacy_agent_catalog,
    resolve_dispatch_agent_profile,
)
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_rerun import JobRerunService
from server.app.services.node_config_batch import run_frozen_payload
from server.app.services.node_profile_pins import agent_version_pin
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import (
    publish_agent_definition,
    replace_agent_catalog,
    save_agent_definition_draft,
)
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


def _seed_catalog() -> None:
    """v1 published, v2 draft (workspace-scoped catalog, schema v46)."""
    replace_agent_catalog(_WORKSPACE, {_AGENT: _v1()})
    save_agent_definition_draft(TEST_DATABASE_URL, _WORKSPACE, _AGENT, _v2())


def _insert_job_rows(job_db, job_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('test-workspace', 'Test')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, 'test-workspace', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'generate')", (job_id,))
        conn.execute(
            "insert into workspace_node_routes("
            "workspace_id, node_key, target_kind, target_id)"
            " values ('test-workspace', 'generate', 'agent', %s)"
            " on conflict(workspace_id, node_key) do nothing",
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


def test_expired_pinned_claim_requeues_with_the_same_version(job_db) -> None:
    """Worker-loss recovery preserves the historical pin and definition hash."""
    _seed_catalog()
    _insert_job_rows(job_db, "job-pinned-recovery")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2"):
        registry.issue_token(
            worker_id=worker_id,
            name=worker_id,
            runtimes=["pi"],
            max_concurrency=1,
            labels={"arch": "arm64"},
            capabilities=["generate"],
            models=[{"provider": "gateway", "model": "test-model"}],
        )
    broker = AgentExecutionBroker(
        TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent, lease_ttl_seconds=1
    )
    execution_id = broker.enqueue(_request("job-pinned-recovery", _v2(), pinned_agent_version=2))
    first = broker.claim("worker-1")
    assert first is not None and first.execution_id == execution_id
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), execution_id),
        )

    assert broker.sweep_expired_claims() == [execution_id]
    second = broker.claim("worker-2")

    assert second is not None
    assert second.execution_id == execution_id
    assert second.lease_id != first.lease_id
    assert second.agent_id == _AGENT
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select state, pinned_agent_version, agent_definition_hash"
            " from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row["state"] == "claimed"
    assert row["pinned_agent_version"] == 2
    assert row["agent_definition_hash"] == _v2().definition_hash()


def test_pre_v93_rerun_keeps_the_frozen_agent_version_pin(job_db, settings) -> None:
    """A legacy snapshot rerun still resolves the run's exact historical Agent."""
    _seed_catalog()
    job_id = "job-pre-v93-rerun"
    _insert_job_rows(job_db, job_id)
    definition = WorkflowDefinition(
        key=_WORKSPACE,
        label="Pre-v93 replay",
        intake=WorkflowIntake(),
        nodes={
            "generate": WorkflowNode(
                key="generate", label="Generate", capability="generate", node_type="agent"
            )
        },
    )
    snapshot = serialize_definition(definition)
    pin = {"agent_id": _AGENT, "version": 2, "definition_hash": _v2().definition_hash()}
    frozen_pins = {
        "quality_replay": {"replay_id": "legacy-replay", "source_job_id": job_id},
        "node_code_versions": {},
        "agent_versions": {"generate": pin},
        "node_profiles": {},
    }
    batch = job_db.create_run(
        _WORKSPACE, "quality_replay", frozen_pins, _WORKSPACE, frozen_pins=frozen_pins
    )
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set run_id=%s, workflow_revision_id='pre-v93-rev',"
            " workflow_version=1, workflow_definition_hash=%s,"
            " workflow_definition_snapshot_json=%s where id=%s",
            (batch["id"], definition_hash(snapshot), snapshot, job_id),
        )
    service = JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
    )

    result = service.rerun(_WORKSPACE, job_id, "generate")

    assert result["status"] == "succeeded"
    rerun_job = job_db.get_job(job_id)
    assert rerun_job["run_id"] == batch["id"]
    assert rerun_job["workflow_definition_snapshot_json"] == snapshot
    payload = run_frozen_payload(job_db, rerun_job)
    assert agent_version_pin(payload, "generate") == pin
    assert _resolve(_WORKSPACE, agent_version_pin(payload, "generate")) == _v2()


def test_unclaimable_sweeper_judges_the_pinned_runtime(job_db) -> None:
    replace_agent_catalog(_WORKSPACE, {_AGENT: _v1()})
    pinned_definition = AgentDefinition(
        capability="generate", runtime="velites", skill="question/generate"
    )
    save_agent_definition_draft(TEST_DATABASE_URL, _WORKSPACE, _AGENT, pinned_definition)
    _insert_job_rows(job_db, "job-unpinned")
    _insert_job_rows(job_db, "job-pinned")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    unpinned_id = broker.enqueue(_request("job-unpinned", _v1()))
    pinned_id = broker.enqueue(_request("job-pinned", pinned_definition, pinned_agent_version=2))
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={},
        capabilities=["generate"],
        models=[{"provider": "gateway", "model": "test-model"}],
    )

    failed = fail_unclaimable_model_requests(broker)

    # A wrong join to published v1 (pi) would keep the pinned velites request
    # queued; the immutable v2 row must drive the sweep's verdict instead.
    assert failed == [pinned_id]
    assert _request_state(pinned_id) == "done"
    assert _request_state(unpinned_id) == "queued"


def test_stale_definition_sweeper_respects_pin(job_db) -> None:
    _seed_catalog()
    _insert_job_rows(job_db, "job-unpinned")
    _insert_job_rows(job_db, "job-pinned")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    unpinned_id = broker.enqueue(_request("job-unpinned", _v1()))
    pinned_id = broker.enqueue(_request("job-pinned", _v1(), pinned_agent_version=1))
    assert unpinned_id is not None and pinned_id is not None

    # Publishing v2 archives v1: the unpinned request goes stale, the pinned
    # archived version stays valid because its immutable row still exists.
    publish_agent_definition(TEST_DATABASE_URL, _WORKSPACE, _AGENT, _v2().definition_hash())
    failed = fail_stale_definition_requests(broker)

    assert failed == [unpinned_id]
    assert _request_state(unpinned_id) == "done"
    assert _request_state(pinned_id) == "queued"


def _resolve(workspace_id: str, pin: dict | None) -> AgentDefinition | None:
    profile = resolve_dispatch_agent_profile(TEST_DATABASE_URL, workspace_id, _AGENT, pin)
    if profile is None:
        return None
    assert profile.source == "agent_definition"
    assert profile.legacy_ref is not None and profile.legacy_ref.agent_id == _AGENT
    return profile.legacy_ref.definition


def test_resolve_dispatch_agent_profile() -> None:
    _seed_catalog()
    assert _resolve(_WORKSPACE, None) == _v1()

    pin = {"agent_id": _AGENT, "version": 2, "definition_hash": _v2().definition_hash()}
    assert _resolve(_WORKSPACE, pin) == _v2()
    pinned = resolve_dispatch_agent_profile(TEST_DATABASE_URL, _WORKSPACE, _AGENT, pin)
    assert pinned is not None and dict(pinned.requires_labels) == {"arch": "arm64"}

    # No global fallback (schema v46): the same agent id is invisible in
    # another workspace.
    assert _resolve("other-workspace", None) is None

    with pytest.raises(ValueError, match="routes to"):
        _resolve(_WORKSPACE, {**pin, "agent_id": "other"})
    with pytest.raises(ValueError, match="does not exist"):
        _resolve(_WORKSPACE, {**pin, "version": 99})
    with pytest.raises(ValueError, match="hash mismatch"):
        _resolve(_WORKSPACE, {**pin, "definition_hash": _v1().definition_hash()})


def test_fresh_legacy_catalog_reads_published_rows_via_job_queries(tmp_path) -> None:
    """#932 R1: the uncached catalog read goes through the JobQueries facade."""
    _seed_catalog()
    job_db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")

    # v2 is only a draft: the published row (v1) is what the catalog serves.
    assert dict(fresh_legacy_agent_catalog(job_db, _WORKSPACE)) == {_AGENT: _v1()}
    assert dict(fresh_legacy_agent_catalog(job_db, "other-workspace")) == {}
