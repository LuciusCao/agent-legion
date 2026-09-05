"""End-to-end claim-path event emission (issue #490, postgres tier): the
broker's real claim transaction must emit claim.granted / claim.empty /
claim.rejected with the decision-point reason codes intact.

The unit tier (test_worker_events.py) pins the emitter shape and the reason
mapping in isolation; these tests prove the wiring: skip reasons collected in
``claim_evaluate`` reach the structured events with no behavior change.
"""

from __future__ import annotations

import json
import logging

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def _seed_queued_request(job_db, job_id: str, *, runtime: str = "pi") -> None:
    definition = AgentDefinition(
        capability="generate",
        runtime=runtime,
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    replace_agent_catalog("test-workspace", {"generator-v1": definition})
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
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'generate')", (job_id,))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values ('test-workspace', 'generate', 'agent', 'generator-v1')"
            " on conflict(workspace_id, node_key) do nothing"
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "execution": {"provider": "gateway", "model": "test-model"},
            },
        )
    )


def _ensure_workspace(job_db, workspace_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, %s, 'demo_workflow') on conflict(id) do nothing",
            (workspace_id, workspace_id),
        )


def _worker(
    job_db,
    worker_id: str,
    *,
    runtimes: list[str] | None = None,
    models: list[dict[str, str]] | None = None,
    max_concurrency: int = 10,
    allowed_workspaces: list[str] | None = None,
) -> None:
    if allowed_workspaces is not None:
        for workspace_id in allowed_workspaces:
            _ensure_workspace(job_db, workspace_id)
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=runtimes if runtimes is not None else ["pi"],
        models=models if models is not None else [{"provider": "gateway", "model": "test-model"}],
        max_concurrency=max_concurrency,
        labels={"arch": "arm64"},
        allowed_workspaces=allowed_workspaces,
    )


@pytest.fixture
def events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    caplog.set_level(logging.DEBUG, logger="agent_legion.worker_events")
    captured: list[dict] = []

    class _View:
        def __getitem__(self, index: int) -> dict:
            return self._live[index]

        def __iter__(self):
            return iter(self._live)

        def __len__(self) -> int:
            return len(self._live)

        @property
        def _live(self) -> list[dict]:
            fresh = [
                json.loads(record.getMessage())
                for record in caplog.records
                if record.name == "agent_legion.worker_events"
            ]
            captured.extend(record for record in fresh if record not in captured)
            return captured

    return _View()


def test_claim_granted_emits_event_with_runtime(job_db, events) -> None:
    _seed_queued_request(job_db, "evt-granted-job")
    _worker(job_db, "evt-hungry")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert broker.claim("evt-hungry") is not None

    granted = [event for event in events if event["event"] == "claim.granted"]
    assert len(granted) == 1
    assert granted[0]["worker_id"] == "evt-hungry"
    assert granted[0]["job_id"] == "evt-granted-job"
    assert granted[0]["runtime"] == "pi"
    assert granted[0]["model"] == "test-model"
    assert granted[0]["kind"] == "agent"
    assert granted[0]["execution_id"]


def test_empty_claim_on_drained_queue_emits_plain_empty(job_db, events) -> None:
    _worker(job_db, "evt-idle")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert broker.claim("evt-idle") is None

    empties = [event for event in events if event["event"] == "claim.empty"]
    assert len(empties) == 1
    assert empties[0]["worker_id"] == "evt-idle"
    assert "reasons" not in empties[0]  # truly drained: no reason noise
    assert not [event for event in events if event["event"] == "claim.rejected"]


def test_model_mismatch_emits_rejected_with_reason(job_db, events) -> None:
    # Stock present; this worker declares a different model → admission
    # rejection (the "model not declared" family from the issue).
    _seed_queued_request(job_db, "evt-model-job")
    _worker(job_db, "evt-wrong-model", models=[{"provider": "gateway", "model": "other-model"}])
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert broker.claim("evt-wrong-model") is None

    rejected = [event for event in events if event["event"] == "claim.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["worker_id"] == "evt-wrong-model"
    assert rejected[0]["reasons"] == {"model_mismatch": 1}


def test_runtime_mismatch_emits_rejected_with_reason(job_db, events) -> None:
    # The definition's runtime (pi) is not in this worker's declarations.
    _seed_queued_request(job_db, "evt-runtime-job", runtime="velites")
    _worker(job_db, "evt-pi-only", runtimes=["pi"])
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    assert broker.claim("evt-pi-only") is None

    rejected = [event for event in events if event["event"] == "claim.rejected"]
    assert rejected[0]["reasons"] == {"runtime_mismatch": 1}


def test_capacity_full_emits_rejected_with_pool_state(job_db, events) -> None:
    # Both pools at their declared cap: the scan is skipped entirely
    # (needed_claim_kinds empty) — still an admission rejection.
    _worker(job_db, "evt-full", max_concurrency=1)
    _seed_queued_request(job_db, "evt-cap-job-a")
    _seed_queued_request(job_db, "evt-cap-job-b")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert broker.claim("evt-full") is not None  # fills the only slot

    assert broker.claim("evt-full") is None  # both pools exhausted

    rejected = [event for event in events if event["event"] == "claim.rejected"]
    assert rejected[0]["worker_id"] == "evt-full"
    # agent pool at capacity: 1 active of 1 declared.
    assert rejected[0]["agent_active"] == 1
    assert rejected[0]["agent_capacity"] == 1


def test_scope_denied_emits_rejected(job_db, events) -> None:
    # A queued request for another workspace this worker is not scoped to.
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    replace_agent_catalog("other-workspace", {"generator-v1": definition})
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('other-workspace', 'Other', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('evt-scope-job', 'other-workspace', 'question', 'evt-scope-job')"
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('evt-scope-job', 'generate')")
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values ('other-workspace', 'generate', 'agent', 'generator-v1')"
            " on conflict(workspace_id, node_key) do nothing"
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert broker.enqueue(
        AgentExecutionRequest(
            workspace_id="other-workspace",
            job_id="evt-scope-job",
            workflow_key="questions",
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": "evt-scope-job",
                "log_path": "logs/evt-scope-job.log",
                "execution": {"provider": "gateway", "model": "test-model"},
            },
        )
    )
    # Worker scoped to test-workspace only (scoped register tokens resolve the
    # scope server-side; the direct registry call passes it explicitly).
    _worker(job_db, "evt-scoped", allowed_workspaces=["test-workspace"])

    assert broker.claim("evt-scoped") is None

    rejected = [event for event in events if event["event"] == "claim.rejected"]
    assert rejected[0]["reasons"] == {"workspace_not_allowed": 1}
