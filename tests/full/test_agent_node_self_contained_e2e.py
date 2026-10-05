"""Full lane (#933, #440 P2): a workspace with NO Agent definitions runs a
self-contained agent node end to end.

Chain under test: revision publish (no route materialized) → the workflow
worker poll pass (the scan gate must open on the self-contained active
revision alone — code_capacity=0 and no online code Worker leave it as the
only reason to scan) → agent dispatch freezes a ``profile_source='node'``
request → a registered Worker claims it on the row's runtime/labels →
release + mark_done close the request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.agent_node_profile_types import (
    profile_from_node,
)
from server.app.services.artifact_store import ArtifactStore
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.helpers import wait_for_predicate
from tests.helpers.executor_worker import make_pi_skill, make_worker
from tests.postgres_support import TEST_DATABASE_URL

_WS = "selfcontained"
_SKILL = "selfcontained/draft"


class _LocalSkillManager:
    """Serves a pre-built skill tree (same double as the velites e2e)."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def checkout_skill(self, skill: str, execution_id: str, ref: str | None = None):
        return self.base_dir / skill, "0" * 40, f"{ref or 'latest'}@{'0' * 12}"

    def cleanup_execution(self, execution_id: str) -> None:
        return None


class _NoLocalExecutor:
    kind = "code"
    id = "code"

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context):  # pragma: no cover - must never run
        raise AssertionError("no code node exists in this workflow")

    def cancel(self, execution_id: str) -> None:
        pass


def _definition(runtime: str = "velites"):
    labels = {"requires_labels": {"arch": "arm64"}} if runtime else {}
    return workflow_definition_from_mapping(
        {
            "key": _WS,
            "label": "Self-contained",
            "execution": {"runtime": runtime, "provider": "gateway", "model": "test-model"},
            "nodes": {
                "draft": {
                    "type": "agent",
                    "capability": "draft",
                    "outputs": ["draft.json"],
                    "skill": {"key": _SKILL},
                    **labels,
                }
            },
        }
    )


@pytest.mark.full_gate
@pytest.mark.parametrize("republish_legacy", [False, True])
def test_workspace_without_agent_definitions_runs_a_self_contained_node(
    tmp_path: Path, job_db, republish_legacy: bool
) -> None:
    """``republish_legacy`` (PR #1039 codex R1): after the job froze the
    self-contained snapshot, the workspace publishes a revision WITHOUT any
    self-contained node — the in-flight job's node must still be enqueued
    (the scan gate also looks at revisions runnable jobs are pinned to)."""
    workspace = job_db.create_workspace("Self-contained", workspace_id=_WS)
    definition = _definition()
    revision = WorkflowRevisionService(job_db, True).save_workspace_revision(_WS, definition)
    with job_db._connect_read() as conn:
        agents = conn.execute(
            "select count(*) as n from versioned_entities where entity_type='agent'"
        ).fetchone()
        routes = conn.execute("select count(*) as n from workspace_node_routes").fetchone()
    assert int(agents["n"]) == 0 and int(routes["n"]) == 0

    snapshot_json = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key=_WS,
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["draft"],
        workspace_id=workspace["id"],
        workflow_definition_hash=definition_hash(snapshot_json),
        workflow_definition_snapshot_json=snapshot_json,
        workflow_revision_id=str(revision["id"]),
    )

    if republish_legacy:
        WorkflowRevisionService(job_db, True).save_workspace_revision(_WS, _definition(""))
        active = job_db.get_active_workflow_revision(_WS, _WS)
        assert active is not None and active["id"] != revision["id"]

    # Pure-remote shape: no local code capacity, no online code Worker — the
    # self-contained active revision is the only thing keeping the scan on.
    worker = make_worker(tmp_path, TEST_DATABASE_URL, _NoLocalExecutor(), [], code_capacity=0)
    worker.state.scan_entries = [(_WS, _WS, definition)]
    worker.code_dispatch = None
    skill_root = tmp_path / "skills"
    make_pi_skill(skill_root, _SKILL)
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    dispatch = AgentDispatchService(
        worker.settings, broker, ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    )
    dispatch.skill_manager = _LocalSkillManager(skill_root)
    worker.agent_dispatch = dispatch

    def _request() -> dict | None:
        worker._poll()
        with job_db._connect_read() as conn:
            row = conn.execute(
                "select * from agent_execution_requests where job_id=%s", (job["id"],)
            ).fetchone()
        return dict(row) if row is not None else None

    wait_for_predicate(lambda: _request() is not None, timeout=20, interval=0.1)
    row = _request()
    assert row is not None
    assert row["profile_source"] == "node"
    assert row["runtime"] == "velites"
    assert row["agent_id"] == "draft"
    assert (
        row["agent_definition_hash"] == profile_from_node(definition.nodes["draft"]).identity_hash()
    )

    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-sc",
        name="worker-sc",
        runtimes=["velites"],
        models=[{"provider": "gateway", "model": "test-model"}],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    claim = broker.claim("worker-sc")
    assert claim is not None
    assert claim.execution_id == row["execution_id"]
    assert claim.runtime == "velites"
    assert claim.manifest["execution"]["provider"] == "gateway"
    assert claim.manifest["command_spec"]["command"][0] == "velites"

    broker.release_slot(claim.execution_id, "worker-sc", claim.lease_id)
    broker.mark_done(claim.execution_id, "worker-sc", claim.lease_id, {"status": "completed"})
    with job_db._connect_read() as conn:
        state = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claim.execution_id,),
        ).fetchone()
        run = conn.execute(
            "select agent_definition_hash from node_runs where id=%s", (claim.node_run_id,)
        ).fetchone()
    assert state["state"] == "done"
    # #645 identity chain: the claim mirrors the profile hash onto node_runs.
    assert run["agent_definition_hash"] == row["agent_definition_hash"]
