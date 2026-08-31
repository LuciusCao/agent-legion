"""live_claim_manifest：claim 时按 revision 实时重解析 execution。

解析链（每个 key）：当前 revision 节点 execution（loader 已合并 workflow
顶层默认）→ enqueue 冻结的 workspace 默认（execution_defaults，legacy
manifest 容错，schema v64 起新 manifest 不再写）→ enqueue 冻结的已解析
execution（兜底）。重取键集合与重校验走 runtime adapter 的
ExecutionContract（issue #75 阶段 2）：revision 升级引入 runtime 不支持的
键或必填键不再可解析时 fail-fast。
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
        "runtime": "velites",
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
def test_new_manifest_without_defaults_resolves_revision_then_frozen() -> None:
    # schema v64 后的新 manifest 没有 execution_defaults：revision 的节点
    # execution（已含合并的顶层默认）优先，节点覆盖被删且顶层无默认时落回
    # enqueue 冻结的 execution。
    manifest = _manifest()
    del manifest["execution_defaults"]
    row = _row(
        manifest=manifest,
        revision_nodes={
            "generate": {
                "capability": "x",
                "execution": {"provider": "top-provider", "model": "top-model"},
            }
        },
    )
    resolved = live_claim_manifest(row)
    assert resolved["execution"]["provider"] == "top-provider"
    assert resolved["execution"]["model"] == "top-model"
    # 节点未覆盖的 key 无默认可落，回退到冻结值。
    assert resolved["execution"]["thinking"] == "low"

    row = _row(manifest=manifest, revision_nodes={"generate": {"capability": "x"}})
    resolved = live_claim_manifest(row)
    assert resolved["execution"]["provider"] == "old-provider"
    assert resolved["execution"]["model"] == "old-model"


@pytest.mark.no_db
def test_revision_node_label_refreshes_manifest_node_label() -> (
    None
):  # node_label 喂给重渲染时自动组装的默认指令：revision 改名实时生效；
    # revision 无 label（或节点缺失）时保留 enqueue 冻结值。
    row = _row(
        manifest=_manifest(node_label="Old Label"),
        revision_nodes={"generate": {"capability": "x", "label": "New Label"}},
    )
    assert live_claim_manifest(row)["node_label"] == "New Label"

    row = _row(
        manifest=_manifest(node_label="Old Label"),
        revision_nodes={"generate": {"capability": "x"}},
    )
    assert live_claim_manifest(row)["node_label"] == "Old Label"


# --- claim 契约化（EXEC-RUNTIME-DISPATCH-001，issue #75 阶段 2）---
#
# 重取键集合 = runtime adapter 的 ExecutionContract；重校验 fail-fast：
# revision 升级引入 runtime 不支持的键，或必填键在所有来源上都不再可解析，
# claim 从静默下发变为可行动报错。


def _narrow_velites_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 velites 的契约换成不含 thinking 的键集合（模拟不支持该键的 runtime）。"""
    import dataclasses

    from server.app.agent_runtime import catalog
    from server.app.agent_runtime.adapter import ExecutionContract, ExecutionKeyRule

    narrowed = dataclasses.replace(
        catalog.get_adapter("velites"),
        execution=ExecutionContract(
            keys={
                "provider": ExecutionKeyRule(required=True, semantics=""),
                "model": ExecutionKeyRule(required=True, semantics=""),
            }
        ),
    )
    monkeypatch.setattr(
        catalog,
        "_ADAPTERS",
        tuple(narrowed if adapter.name == "velites" else adapter for adapter in catalog._ADAPTERS),
    )


@pytest.mark.no_db
def test_claim_fails_fast_when_revision_introduces_unsupported_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # revision 升级后节点配置了 runtime 不支持的键：静默下发 → 可行动报错，
    # 报错含 node.key / runtime / 键名。
    _narrow_velites_contract(monkeypatch)
    manifest = _manifest()
    del manifest["execution_defaults"]
    manifest["execution"]["thinking"] = ""
    row = _row(
        manifest=manifest,
        revision_nodes={
            "generate": {"capability": "x", "execution": {"thinking": "high"}},
        },
    )
    with pytest.raises(ValueError, match=r"node generate configures execution\.thinking"):
        live_claim_manifest(row)


@pytest.mark.no_db
def test_claim_fails_fast_on_frozen_unsupported_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # 节点未新配，但 enqueue 冻结值带着 runtime 不支持的键：同样 fail-fast。
    _narrow_velites_contract(monkeypatch)
    manifest = _manifest()
    del manifest["execution_defaults"]
    row = _row(manifest=manifest, revision_nodes={"generate": {"capability": "x"}})
    with pytest.raises(ValueError, match=r"execution\.thinking.*'velites'"):
        live_claim_manifest(row)


@pytest.mark.no_db
def test_claim_passes_when_unsupported_key_empty_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 空值 = 未配置：窄契约下 thinking 全空放行，且重取只覆盖契约键。
    _narrow_velites_contract(monkeypatch)
    manifest = _manifest()
    del manifest["execution_defaults"]
    manifest["execution"]["thinking"] = ""
    row = _row(
        manifest=manifest,
        revision_nodes={
            "generate": {
                "capability": "x",
                "execution": {"provider": "new-provider", "model": "new-model"},
            }
        },
    )
    resolved = live_claim_manifest(row)
    assert resolved["execution"]["provider"] == "new-provider"
    assert resolved["execution"]["model"] == "new-model"


@pytest.mark.no_db
def test_claim_fails_fast_when_required_key_unresolvable() -> None:
    # 必填键在 revision/defaults/冻结值三层都为空：静默下发空 provider →
    # 可行动报错（legacy manifest 容错路径）。
    manifest = _manifest()
    del manifest["execution_defaults"]
    manifest["execution"]["provider"] = ""
    row = _row(manifest=manifest, revision_nodes={"generate": {"capability": "x"}})
    with pytest.raises(ValueError, match="node generate requires a provider"):
        live_claim_manifest(row)


@pytest.mark.no_db
def test_manifest_without_runtime_falls_back_to_definition_join() -> None:
    # 手工/legacy manifest 可能缺 runtime 键：契约校验与重取改用 definition
    # join 的 row runtime（不写回 manifest，保持与旧行为一致）。
    manifest = _manifest()
    del manifest["runtime"]
    row = _row(manifest=manifest, revision_nodes={"generate": {"capability": "x"}})
    row["runtime"] = "velites"
    resolved = live_claim_manifest(row)
    assert "runtime" not in resolved
    assert resolved["execution"]["provider"] == "ws-provider"


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
