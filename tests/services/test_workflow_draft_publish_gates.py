"""Publish and execution-chain coverage for optional agent capability (#936)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.agent_node_profile_types import profile_from_node
from server.app.services.artifact_store import ArtifactStore
from server.app.services.workflow_draft_publish import validate_workflow_draft_for_publish
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflow_worker.routing import resolve_node_route
from tests.helpers import wait_for_predicate
from tests.helpers.executor_worker import make_pi_skill, make_worker
from tests.postgres_support import TEST_DATABASE_URL

_WS = "capability_optional"
_SKILL = "capability_optional/draft"


class _LocalSkillManager:
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

    def execute(self, context):  # pragma: no cover - this workflow has no code node
        raise AssertionError("the self-contained agent must not enter the code pool")

    def cancel(self, execution_id: str) -> None:
        pass


def _yaml(capability: str | None = None, *, runtime: str | None = "velites") -> str:
    node: dict[str, Any] = {
        "type": "agent",
        "label": "Draft display",
        "outputs": ["draft.json"],
        "skill": {"key": _SKILL},
        "tools": ["read"],
        "requires_labels": {"arch": "arm64"},
        "config_schema": {
            "type": "object",
            "properties": {"tone": {"type": "string", "default": "calm"}},
        },
    }
    if capability is not None:
        node["capability"] = capability
    if runtime is not None:
        node["execution"] = {
            "runtime": runtime,
            "provider": "gateway",
            "model": "test-model",
        }
    return yaml.safe_dump(
        {"key": _WS, "label": "Capability optional", "nodes": {"draft": node}},
        allow_unicode=True,
        sort_keys=False,
    )


def _workspace(job_db) -> None:
    job_db.create_workspace("Capability optional", workspace_id=_WS)


def _skill_base(tmp_path: Path) -> Path:
    base = tmp_path / "skills"
    make_pi_skill(base, _SKILL)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "-C", str(base / _SKILL), "init", "-q"], check=True, env=env)
    return base


def _errors(job_db, tmp_path: Path, definition_yaml: str) -> list[str]:
    return validate_workflow_draft_for_publish(
        job_db,
        _WS,
        definition_yaml,
        True,
        skill_base_dir=_skill_base(tmp_path),
    )


@pytest.mark.parametrize("capability", [None, "", "generate"])
def test_publish_accepts_empty_omitted_and_non_empty_agent_capability(
    job_db, tmp_path: Path, capability: str | None
) -> None:
    _workspace(job_db)

    assert _errors(job_db, tmp_path, _yaml(capability)) == []


def test_publish_still_rejects_an_agent_without_effective_runtime(job_db, tmp_path: Path) -> None:
    _workspace(job_db)

    errors = _errors(job_db, tmp_path, _yaml("", runtime=None))

    assert any("execution.runtime" in error for error in errors)


def test_publish_still_rejects_an_incomplete_agent_skill_profile(job_db, tmp_path: Path) -> None:
    _workspace(job_db)
    raw = yaml.safe_load(_yaml())
    del raw["nodes"]["draft"]["skill"]

    errors = _errors(job_db, tmp_path, yaml.safe_dump(raw))

    assert any("declares no skill" in error for error in errors)


def test_empty_capability_publish_dispatch_claim_uses_node_identity_not_a_definition(
    job_db, tmp_path: Path
) -> None:
    """Real publish → worker routing/dispatch → broker claim, with no Agent rows."""
    _workspace(job_db)
    definition_yaml = _yaml()
    assert _errors(job_db, tmp_path, definition_yaml) == []
    definition = workflow_definition_from_yaml_string(definition_yaml)
    node = definition.nodes["draft"]
    revision = WorkflowRevisionService(job_db, True).save_workspace_revision(_WS, definition)
    snapshot_json = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key=_WS,
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["draft"],
        workspace_id=_WS,
        workflow_definition_hash=definition_hash(snapshot_json),
        workflow_definition_snapshot_json=snapshot_json,
        workflow_revision_id=str(revision["id"]),
    )
    with job_db._connect_read() as conn:
        agents = conn.execute(
            "select count(*) as n from versioned_entities where entity_type='agent'"
        ).fetchone()
        routes = conn.execute("select count(*) as n from workspace_node_routes").fetchone()
    assert int(agents["n"]) == 0 and int(routes["n"]) == 0

    worker = make_worker(tmp_path, TEST_DATABASE_URL, _NoLocalExecutor(), [], code_capacity=0)
    worker.state.scan_entries = [(_WS, _WS, definition)]
    worker.code_dispatch = None
    skill_root = tmp_path / "skills"
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
    dispatch = AgentDispatchService(
        worker.settings,
        broker,
        ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL),
    )
    dispatch.skill_manager = _LocalSkillManager(skill_root)
    worker.agent_dispatch = dispatch

    route = resolve_node_route(worker, _WS, _WS, node)
    assert (route.kind, route.target_id, route.profile_source) == ("agent", "draft", "node")

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
    assert row["agent_definition_hash"] == profile_from_node(node).identity_hash()
    manifest = json.loads(row["manifest_json"])
    assert manifest["capability"] == ""
    assert manifest["agent_id"] == "draft"
    assert manifest["node_key"] == "draft"
    assert manifest["node_label"] == "Draft display"

    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-capability-optional",
        name="worker-capability-optional",
        runtimes=["velites"],
        models=[{"provider": "gateway", "model": "test-model"}],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    claim = broker.claim("worker-capability-optional")

    assert claim is not None
    assert claim.execution_id == row["execution_id"]
    assert claim.runtime == "velites"
    assert claim.manifest["capability"] == ""
    assert claim.manifest["agent_id"] == "draft"
    assert claim.manifest["node_label"] == "Draft display"
    assert claim.manifest["command_spec"]["command"][0] == "velites"

    broker.release_slot(claim.execution_id, "worker-capability-optional", claim.lease_id)
    broker.mark_done(
        claim.execution_id,
        "worker-capability-optional",
        claim.lease_id,
        {"status": "completed"},
    )
