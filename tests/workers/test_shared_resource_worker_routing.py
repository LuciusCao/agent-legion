from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.workflow_worker import code_claim
from server.app.workflows.schema import WorkflowNode
from worker.code_runner import build_child_payload

pytestmark = pytest.mark.no_db


class _EnqueuePool:
    def submit(self, fn):
        fn()
        return True


class _Vault:
    def __init__(self, *args, **kwargs):
        pass

    def resolve_secret_refs(self, values, workspace_id):
        return dict(values)


def _route(
    monkeypatch, tmp_path: Path, node: WorkflowNode, shard_runtime, *, shared_capable: bool = True
):
    enqueued = []
    online_calls = []
    dispatch = SimpleNamespace(
        broker=SimpleNamespace(has_active_request=lambda *args, **kwargs: False),
        is_in_flight=lambda job_id, key: False,
        online_code_worker_available=lambda capability, workspace_id: (
            online_calls.append((capability, workspace_id)) or True
        ),
        online_etag_capable_code_worker_available=lambda workspace_id, minimum_protocol: (
            shared_capable
        ),
        try_mark_in_flight=lambda job_id, key: True,
        discard_in_flight=lambda job_id, key: None,
        enqueue_pool=_EnqueuePool(),
        enqueue=lambda **kwargs: enqueued.append(kwargs),
    )
    worker = SimpleNamespace(
        code_dispatch=dispatch,
        code_stock=SimpleNamespace(allows=lambda: True),
        settings=SimpleNamespace(
            root_dir=str(tmp_path),
            config={},
            executor_runtime=SimpleNamespace(workflows=SimpleNamespace(custom_nodes_enabled=True)),
        ),
        state=SimpleNamespace(pass_claim_counts={}),
        job_db=object(),
    )
    monkeypatch.setattr(code_claim, "etag_gate_blocks_remote_enqueue", lambda *args: False)
    monkeypatch.setattr(code_claim, "cached_run_payload", lambda *args: {})
    monkeypatch.setattr(code_claim, "resolve_dispatch_node_code", lambda *args: "def run(): pass")
    monkeypatch.setattr(code_claim, "is_worker_eligible", lambda *args: True)
    monkeypatch.setattr(code_claim, "VaultService", _Vault)
    monkeypatch.setattr(code_claim, "inject_connection_config", lambda *args: None)
    handled = code_claim.try_claim_code_worker_node(
        worker,
        {"id": "ws"},
        {"id": "job-1"},
        node,
        tmp_path / "job",
        tmp_path / "run.log",
        (),
        "ws",
        shard_runtime=shard_runtime,
    )
    return handled, enqueued, online_calls


def _node(*, shared_resources=()) -> WorkflowNode:
    return WorkflowNode(
        key="draft",
        label="Draft",
        capability="",
        shared_resources=shared_resources,
    )


@pytest.mark.parametrize(
    "shard_runtime",
    [None, {"shard_index": 0, "shard_input": {"value": 1}}],
)
def test_remote_enqueue_supports_shared_resources_for_current_worker(
    monkeypatch, tmp_path: Path, shard_runtime
) -> None:
    handled, enqueued, online_calls = _route(
        monkeypatch,
        tmp_path,
        _node(shared_resources=("resource-1",)),
        shard_runtime,
    )
    assert handled is True
    assert online_calls == [("", "ws")]
    assert len(enqueued) == 1
    assert enqueued[0]["node"].shared_resources == ("resource-1",)


def test_shared_resources_fall_back_to_host_when_only_old_workers_are_online(
    monkeypatch, tmp_path: Path
) -> None:
    handled, enqueued, _ = _route(
        monkeypatch,
        tmp_path,
        _node(shared_resources=("resource-1",)),
        None,
        shared_capable=False,
    )
    assert handled is False
    assert enqueued == []


@pytest.mark.parametrize(
    "shard_runtime",
    [None, {"shard_index": 0, "shard_input": {"value": 1}}],
)
def test_mixed_workflow_plain_node_still_executes_remotely(
    monkeypatch, tmp_path: Path, shard_runtime
) -> None:
    handled, enqueued, online_calls = _route(monkeypatch, tmp_path, _node(), shard_runtime)
    assert handled is True
    assert online_calls == [("", "ws")]
    assert len(enqueued) == 1


def _manifest(node_key: str = "draft") -> dict:
    return {
        "node_key": node_key,
        "runtime_context": {
            "job": {"id": "job-1"},
            "workspace": {},
            "settings_config": {},
            "job_batch": None,
            "skill_versions": {},
        },
    }


def _mixed_snapshot() -> str:
    return json.dumps(
        {
            "nodes": {
                "draft": {"shared_resources": ["resource-1"]},
                "plain": {"shared_resources": []},
            }
        }
    )


@pytest.mark.parametrize("shard", [False, True])
def test_worker_mixed_snapshot_materializes_only_the_declared_node(
    tmp_path: Path, shard: bool
) -> None:
    plain = _manifest("plain")
    plain["runtime_context"]["job"]["workflow_definition_snapshot_json"] = _mixed_snapshot()
    if shard:
        plain.update({"shard_index": 0, "shard_input": {"value": 1}})
    payload = build_child_payload(plain, "def run(): pass", tmp_path)
    assert payload["runtime"]["node_key"] == "plain"
    assert ("shard_index" in payload["runtime"]) is shard

    declared = _manifest("draft")
    declared["runtime_context"]["job"]["workflow_definition_snapshot_json"] = _mixed_snapshot()
    resources = {"resource-1": {"version": 1, "files": []}}
    payload = build_child_payload(declared, "def run(): pass", tmp_path, shared_resources=resources)
    assert payload["runtime"]["shared_resources"] == resources
    assert (
        "shared_resources" not in build_child_payload(plain, "def run(): pass", tmp_path)["runtime"]
    )


def test_worker_allows_business_keys_and_run_level_pins(tmp_path: Path) -> None:
    manifest = _manifest("plain")
    manifest["config"] = {"shared_resources": {"custom": "value"}}
    manifest["runtime_context"]["job"].update(
        {
            "workflow_definition_snapshot_json": _mixed_snapshot(),
            "input_json": json.dumps({"shared_resources": {"custom": "item"}}),
        }
    )
    manifest["runtime_context"]["job_batch"] = {
        "frozen_pins_json": json.dumps(
            {"shared_resources": {"pins": [{"resource_id": "resource-1"}]}}
        ),
        "source_payload_json": json.dumps(
            {"task_candidates": [{"shared_resources": {"custom": "payload"}}]}
        ),
    }
    payload = build_child_payload(manifest, "def run(): pass", tmp_path)
    assert payload["runtime"]["node_config"] == {"shared_resources": {"custom": "value"}}


@pytest.mark.parametrize(
    "payload_location",
    ["manifest", "runtime_context"],
)
def test_worker_rejects_platform_resource_payload(tmp_path: Path, payload_location: str) -> None:
    manifest = _manifest("plain")
    target = manifest if payload_location == "manifest" else manifest["runtime_context"]
    target["shared_resources"] = {"resource-1": {"version": 1}}
    with pytest.raises(ValueError, match="shared resource"):
        build_child_payload(manifest, "def run(): pass", tmp_path)


def test_worker_rejects_current_node_declaration_without_materialization(tmp_path: Path) -> None:
    manifest = _manifest("draft")
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = json.dumps(
        {"nodes": {"draft": {"shared_resources": ["resource-1"]}}}
    )
    with pytest.raises(ValueError, match="missing or do not match"):
        build_child_payload(manifest, "def run(): pass", tmp_path)


def test_worker_allows_empty_shared_resource_declaration(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = json.dumps(
        {"nodes": {"draft": {"shared_resources": []}}}
    )
    payload = build_child_payload(manifest, "def run(): pass", tmp_path)
    assert "shared_resources" not in payload["runtime"]
