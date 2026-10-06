"""Agent dispatch by profile source (#933, #440 P2).

- legacy (Agent-definition) dispatch replays byte-for-byte against the
  manifest recorded on release/0.7.16 before P2 (the only row-level change
  is the defaulted ``profile_source`` column, asserted on the request);
- self-contained nodes freeze their own profile: the request row carries
  ``profile_source='node'``, runtime and requires_labels, ``agent_id`` is
  the node key and ``agent_definition_hash`` the profile hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from server.app.agent_broker import dispatch as agent_dispatch
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_node_profile_types import (
    profile_from_node,
)
from server.app.settings import Settings
from server.app.skills.checkout import SkillCheckout
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution, WorkflowNodeSkill

pytestmark = pytest.mark.no_db

_GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "fixtures/agent_profile/legacy_dispatch_manifest_0716.json"
)
_SCHEMA = {
    "type": "object",
    "properties": {
        "page_size": {"type": "integer"},
        "api_key": {"type": "string", "secret": True},
    },
}


def _node(*, runtime: str = "", labels: dict[str, str] | None = None) -> WorkflowNode:
    return WorkflowNode(
        key="generate",
        label="Generate",
        capability="generate",
        node_type="agent",
        inputs=["question.json"],
        outputs=["answer.json"],
        skill=WorkflowNodeSkill(key="question/generate", ref="v1"),
        tools=("read", "write") if runtime else (),
        requires_labels=labels or {},
        config_schema=_SCHEMA if runtime else {},
        execution=WorkflowNodeExecution(
            provider="node-provider",
            model="node-model",
            thinking="high",
            prompt="Answer carefully",
            runtime=runtime,
        ),
    )


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, MagicMock, dict]:
    captured: dict[str, Any] = {}
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    checkout = SkillCheckout(
        key="question/generate",
        ref="v1",
        run_dir=skill_dir,
        commit="c" * 40,
        version="v1@cccccccccccc",
    )
    monkeypatch.setattr(agent_dispatch, "build_skill_manager", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(agent_dispatch, "AgentEnqueuePool", lambda **_k: MagicMock())
    monkeypatch.setattr(agent_dispatch, "checkout_node_skill", lambda *_a, **_k: checkout)
    monkeypatch.setattr(
        agent_dispatch,
        "stage_agent_inputs",
        lambda _store, context, _manifest: captured.setdefault("context", context),
    )
    monkeypatch.setattr(
        agent_dispatch, "build_agent_bundle", lambda path, **_k: path.write_text("bundle")
    )
    monkeypatch.setattr(
        agent_dispatch.uuid, "uuid4", lambda: "00000000-0000-0000-0000-000000000123"
    )
    broker = MagicMock()
    broker.bundle_dir = tmp_path
    broker.has_active_request.return_value = False
    broker.enqueue.return_value = "queued"
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path,
        logs_dir=tmp_path,
        packages_dir=tmp_path,
        jobs_dir=tmp_path,
        config={},
    )
    return AgentDispatchService(settings, broker, MagicMock()), broker, captured


def _enqueue(service: Any, *, agent_id: str, definition: AgentDefinition, node: WorkflowNode, **kw):
    return service.enqueue(
        agent_id=agent_id,
        definition=definition,
        workspace={"id": "workspace-1"},
        job={"id": "job-1"},
        workflow_key="workspace-1",
        node=node,
        job_dir=Path("/golden/jobs/job-1"),
        log_path=Path("/golden/logs/job-1-generate.log"),
        inputs=("question.json",),
        node_config={"page_size": 25, "api_key": "x", "timeout_seconds": 900},
        **kw,
    )


def test_legacy_dispatch_replays_the_recorded_0716_manifest_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recorded on release/0.7.16 (pre-P2): same node + published definition
    must freeze the identical manifest JSON (as enqueue persists it), the
    identical node_execution runtime block (no ``runtime`` key) and the
    identical identity columns."""
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    service, broker, captured = _service(tmp_path, monkeypatch)
    definition = AgentDefinition(
        capability="generate",
        runtime="velites",
        tools=("read", "write"),
        requires_labels={"gpu": "yes"},
        config_schema=_SCHEMA,
    )

    _enqueue(service, agent_id="generator-v1", definition=definition, node=_node())

    request = broker.enqueue.call_args.args[0]
    manifest = dict(request.manifest)
    manifest.pop("bundle_name")
    assert json.dumps(manifest, ensure_ascii=False, sort_keys=True) == golden["manifest_json"]
    assert captured["context"].runtime["node_execution"] == golden["node_execution"]
    assert request.agent_id == golden["agent_id"]
    assert request.agent_definition_hash == golden["agent_definition_hash"]
    # Only the new defaulted columns differ from the pre-P2 row shape.
    assert request.profile_source == "agent_definition"
    assert request.runtime is None
    assert request.requires_labels is None


def test_self_contained_dispatch_freezes_the_node_profile_on_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, broker, captured = _service(tmp_path, monkeypatch)
    node = _node(runtime="velites", labels={"gpu": "yes"})
    profile = profile_from_node(node)

    _enqueue(
        service,
        agent_id=node.key,
        definition=profile.dispatch_definition,
        node=node,
        profile_source="node",
    )

    request = broker.enqueue.call_args.args[0]
    assert request.profile_source == "node"
    assert request.runtime == "velites"
    assert dict(request.requires_labels) == {"gpu": "yes"}
    assert request.agent_id == "generate"
    assert request.agent_definition_hash == profile.identity_hash()
    manifest = dict(request.manifest)
    assert manifest["runtime"] == "velites"
    assert manifest["capability"] == "generate"
    assert manifest["agent_id"] == "generate"
    assert manifest["tools"] == ["read", "write"]
    # Node config_schema governs the manifest whitelist (secret key dropped).
    assert manifest["config"] == {"page_size": 25}
    assert captured["context"].executor_id == "agent:generate"
    assert captured["context"].runtime["node_execution"]["runtime"] == "velites"
