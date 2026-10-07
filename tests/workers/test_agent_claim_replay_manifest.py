"""Replay profile pin through the worker → enqueue → claim chain (#1079, D6).

``claim_agent_node`` must hand the run's ``node_profiles`` pin to
``AgentDispatchService.enqueue``, which writes it into the queued manifest;
the broker's ``live_claim_manifest`` then keeps the chosen execution / prompt
instead of re-reading the copy job's original revision. Any link dropped
silently re-runs the original revision's model and prompt, so the chain is
pinned end to end here (real worker claim + real dispatch, fake broker).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from server.app.agent_broker import dispatch as agent_dispatch
from server.app.agent_broker.agent_claim_compatibility import live_claim_manifest
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.services.agent_node_profile_types import PROFILE_SOURCE_NODE
from server.app.services.node_profile_pins import MANIFEST_KEY, PIN_KEY, node_profile_hash
from server.app.settings import Settings
from server.app.skills.checkout import SkillCheckout
from server.app.workflow_worker.agent_claim import claim_agent_node
from server.app.workflow_worker.state import WorkflowWorkerState
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution

pytestmark = pytest.mark.no_db

# The copy job's snapshot node: the chosen (transplanted) profile.
_CHOSEN = WorkflowNode(
    key="generate",
    label="Generate",
    capability="generate",
    node_type="agent",
    inputs=["question.json"],
    outputs=["answer.json"],
    execution=WorkflowNodeExecution(
        runtime="pi", provider="gateway", model="model-b", prompt="PROMPT-B"
    ),
)

# The ORIGINAL revision the copy job still links to (lineage).
_ORIGINAL_REVISION = {
    "nodes": {
        "generate": {
            "label": "Generate",
            "execution": {
                "runtime": "velites",
                "provider": "gateway",
                "model": "model-a",
                "prompt": "PROMPT-A",
            },
        }
    }
}


def _dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, MagicMock]:
    checkout = SkillCheckout(key="", ref="", run_dir=tmp_path, commit="", version="")
    monkeypatch.setattr(agent_dispatch, "build_skill_manager", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(agent_dispatch, "AgentEnqueuePool", lambda **_k: MagicMock())
    monkeypatch.setattr(agent_dispatch, "checkout_node_skill", lambda *_a, **_k: checkout)
    monkeypatch.setattr(agent_dispatch, "stage_agent_inputs", lambda *_a, **_k: None)
    monkeypatch.setattr(
        agent_dispatch, "build_agent_bundle", lambda path, **_k: path.write_text("bundle")
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
    service = AgentDispatchService(settings, broker, MagicMock())
    # Run the enqueue closure inline (no thread pool).
    service.enqueue_pool = SimpleNamespace(submit=lambda fn: (fn(), True)[1])
    return service, broker


def _claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_payload: dict) -> MagicMock:
    service, broker = _dispatch(tmp_path, monkeypatch)
    failures: list[str] = []
    state = WorkflowWorkerState()
    state.batch_payload_cache["run-1"] = run_payload
    worker = SimpleNamespace(
        state=state,
        agent_dispatch=service,
        job_db=None,
        leases=SimpleNamespace(fail_without_lease=lambda _req, msg: failures.append(msg)),
    )
    claimed = claim_agent_node(
        cast(Any, worker),
        {"id": "ws-1"},
        {"id": "job-1", "run_id": "run-1"},
        _CHOSEN,
        tmp_path,
        tmp_path / "generate.log",
        ("question.json",),
        _CHOSEN.key,
        "ws-1",
        profile_source=PROFILE_SOURCE_NODE,
    )
    assert failures == []
    assert claimed is True
    return broker


def _pin() -> dict[str, Any]:
    return {
        "revision_id": "ws-1:v2",
        "node_key": "generate",
        "profile_hash": node_profile_hash(_CHOSEN),
    }


def test_pinned_run_enqueues_the_pin_and_claims_the_chosen_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = _claim(tmp_path, monkeypatch, {PIN_KEY: {"generate": _pin()}, "node_config": {}})

    request = broker.enqueue.call_args.args[0]
    manifest = dict(request.manifest)
    assert manifest[MANIFEST_KEY] == _pin()

    # Broker claim against the copy job's ORIGINAL revision link.
    claimed = live_claim_manifest(
        {
            "manifest_json": json.dumps(manifest),
            "node_key": "generate",
            "runtime": "pi",
            "revision_definition_json": json.dumps(_ORIGINAL_REVISION),
        }
    )
    assert claimed["execution"]["model"] == "model-b"
    assert claimed["additional_prompt"] == "PROMPT-B"


def test_unpinned_run_keeps_the_live_revision_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: without a pin nothing is written and the revision wins at claim."""
    broker = _claim(tmp_path, monkeypatch, {"node_config": {}})

    manifest = dict(broker.enqueue.call_args.args[0].manifest)
    assert MANIFEST_KEY not in manifest
    claimed = live_claim_manifest(
        {
            "manifest_json": json.dumps(manifest),
            "node_key": "generate",
            "runtime": "pi",
            "revision_definition_json": json.dumps(_ORIGINAL_REVISION),
        }
    )
    assert claimed["execution"]["model"] == "model-a"
    assert claimed["additional_prompt"] == "PROMPT-A"
