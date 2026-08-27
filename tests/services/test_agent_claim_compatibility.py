"""live_claim_manifest：claim 时按 revision 实时重解析 execution。

解析链（每个 key）：当前 revision 节点覆盖 → enqueue 冻结的 workspace 默认
（execution_defaults）→ enqueue 冻结的已解析 execution（旧 manifest 兜底）。
"""

from __future__ import annotations

import json

import pytest

from server.app.agent_broker.agent_claim_compatibility import live_claim_manifest, worker_can_run
from server.app.agent_control.declarations import normalize_models


def _row(
    *,
    manifest: dict,
    revision_nodes: dict | None = None,
) -> dict:
    row: dict = {
        "manifest_json": json.dumps(manifest),
        "node_key": "generate",
        "revision_definition_json": None,
    }
    if revision_nodes is not None:
        row["revision_definition_json"] = json.dumps({"nodes": revision_nodes})
    return row


def _manifest(**overrides) -> dict:
    manifest = {
        "execution": {
            "binary": "velites",
            "provider": "old-provider",
            "model": "old-model",
            "thinking": "low",
            "timeout_seconds": 1800,
            "no_sandbox": False,
        },
        "execution_defaults": {
            "provider": "ws-provider",
            "model": "ws-model",
            "thinking": "medium",
        },
    }
    manifest.update(overrides)
    return manifest


@pytest.mark.no_db
def test_removed_node_override_falls_back_to_enqueue_workspace_defaults() -> None:
    # enqueue 时节点有覆盖（已烘焙进 execution），升级 revision 后覆盖被移除：
    # 必须落回 enqueue 时的 workspace 默认，而不是旧的烘焙覆盖。
    row = _row(manifest=_manifest(), revision_nodes={"generate": {"capability": "x"}})

    resolved = live_claim_manifest(row)

    assert resolved["execution"]["provider"] == "ws-provider"
    assert resolved["execution"]["model"] == "ws-model"
    assert resolved["execution"]["thinking"] == "medium"


@pytest.mark.no_db
def test_live_node_override_wins_over_frozen_values() -> None:
    row = _row(
        manifest=_manifest(),
        revision_nodes={
            "generate": {
                "capability": "x",
                "execution": {"provider": "node-provider", "model": "node-model"},
            }
        },
    )

    resolved = live_claim_manifest(row)

    assert resolved["execution"]["provider"] == "node-provider"
    assert resolved["execution"]["model"] == "node-model"
    # 节点未覆盖的 key 仍走 workspace 默认。
    assert resolved["execution"]["thinking"] == "medium"


@pytest.mark.no_db
def test_legacy_manifest_without_execution_defaults_uses_frozen_fallback() -> None:
    manifest = _manifest()
    del manifest["execution_defaults"]
    row = _row(manifest=manifest, revision_nodes={"generate": {"capability": "x"}})

    resolved = live_claim_manifest(row)

    assert resolved["execution"]["provider"] == "old-provider"
    assert resolved["execution"]["model"] == "old-model"
    assert resolved["execution"]["thinking"] == "low"


@pytest.mark.no_db
def test_missing_revision_keeps_defaults_then_frozen() -> None:
    row = _row(manifest=_manifest(), revision_nodes=None)

    resolved = live_claim_manifest(row)

    assert resolved["execution"]["provider"] == "ws-provider"
    assert resolved["execution"]["model"] == "ws-model"


@pytest.mark.no_db
def test_model_declaration_is_scoped_to_agent_runtime() -> None:
    candidate = {"runtime": "velites", "capability": "review"}
    manifest = {"execution": {"provider": "sqai", "model": "kimi"}}

    assert not worker_can_run(
        candidate,
        manifest,
        {"review"},
        {("pi", "sqai", "kimi")},
    )
    assert worker_can_run(
        candidate,
        manifest,
        {"review"},
        {("velites", "sqai", "kimi")},
    )


@pytest.mark.no_db
def test_legacy_unscoped_model_declaration_remains_compatible() -> None:
    assert worker_can_run(
        {"runtime": "velites", "capability": "review"},
        {"execution": {"provider": "sqai", "model": "kimi"}},
        {"review"},
        {("*", "sqai", "kimi")},
    )


@pytest.mark.no_db
def test_protocol_v3_model_declaration_requires_runtime() -> None:
    with pytest.raises(ValueError, match="protocol v3"):
        normalize_models(
            [{"provider": "sqai", "model": "kimi"}],
            ["velites"],
            require_runtime=True,
        )
