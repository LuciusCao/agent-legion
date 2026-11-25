from __future__ import annotations

import copy
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


@pytest.mark.parametrize(
    "shard_runtime",
    [None, {"shard_index": 0, "shard_input": {"value": 1}}],
)
def test_remote_enqueue_falls_back_to_host_for_shared_resources(
    monkeypatch, tmp_path: Path, shard_runtime
) -> None:
    enqueued = []
    online_calls = []
    dispatch = SimpleNamespace(
        broker=SimpleNamespace(has_active_request=lambda *args, **kwargs: False),
        is_in_flight=lambda job_id, key: False,
        online_code_worker_available=lambda capability, workspace_id: (
            online_calls.append((capability, workspace_id)) or True
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
    node = WorkflowNode(
        key="draft",
        label="Draft",
        capability="",
        shared_resources=("resource-1",),
    )

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

    assert handled is False
    assert online_calls == [("", "ws")]
    assert enqueued == []


def _manifest() -> dict:
    return {
        "runtime_context": {
            "job": {"id": "job-1"},
            "workspace": {},
            "settings_config": {},
            "job_batch": None,
            "skill_versions": {},
        }
    }


@pytest.mark.parametrize(
    "resource_patch",
    [
        {"shared_resources": {"resource-1": {"version": 1}}},
        {
            "runtime_context": {
                "shared_resources": {"resource-1": {"version": 1}},
            }
        },
        {
            "runtime_context": {
                "job": {
                    "workflow_definition_snapshot_json": json.dumps(
                        {"nodes": {"draft": {"shared_resources": ["resource-1"]}}}
                    )
                }
            }
        },
        {
            "runtime_context": {
                "job_batch": {
                    "frozen_pins_json": json.dumps(
                        {"shared_resources": {"pins": [{"resource_id": "resource-1"}]}}
                    )
                }
            }
        },
    ],
)
def test_worker_rejects_shared_resource_declarations_and_payloads(
    tmp_path: Path, resource_patch
) -> None:
    manifest = _manifest()
    for key, value in resource_patch.items():
        if key == "runtime_context":
            manifest[key].update(copy.deepcopy(value))
        else:
            manifest[key] = copy.deepcopy(value)

    with pytest.raises(ValueError, match="require Host execution"):
        build_child_payload(manifest, "def run(): pass", tmp_path)


def test_worker_allows_empty_shared_resource_declaration(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["runtime_context"]["job"]["workflow_definition_snapshot_json"] = json.dumps(
        {"nodes": {"draft": {"shared_resources": []}}}
    )
    payload = build_child_payload(manifest, "def run(): pass", tmp_path)
    assert "shared_resources" not in payload["runtime"]
