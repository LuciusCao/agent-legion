"""ExecutionContract：adapter 声明与 dispatch/claim 共用校验（issue #75 阶段 2）。

契约表（方案 §各 adapter execution 契约）：pi/velites provider+model 必填、
thinking 可选；openclaw model 必填、provider 可选（拼 model 串）、thinking
可选。契约表外的键配置了非空值即 fail-fast，报错含 node.key / runtime / 键名。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import pytest

from server.app.agent_runtime import catalog
from server.app.agent_runtime.adapter import ExecutionContract, ExecutionKeyRule
from server.app.agent_runtime.catalog import get_adapter
from server.app.agent_runtime.execution import resolve_execution, validate_execution_contract
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution

pytestmark = pytest.mark.no_db

# runtime -> {key: required}
CONTRACT_TABLE: dict[str, dict[str, bool]] = {
    "pi": {"provider": True, "model": True, "thinking": False},
    "velites": {"provider": True, "model": True, "thinking": False},
    "openclaw": {"provider": False, "model": True, "thinking": False},
}


def _node(provider: str = "", model: str = "", thinking: str = "") -> WorkflowNode:
    return WorkflowNode(
        key="gen",
        label="gen",
        capability="generate",
        execution=WorkflowNodeExecution(provider=provider, model=model, thinking=thinking),
    )


def _narrow_contract(
    monkeypatch: pytest.MonkeyPatch, runtime: str, keys: Mapping[str, ExecutionKeyRule]
) -> None:
    """把某个已注册 adapter 的契约换成更窄的键集合（模拟不支持某键的 runtime）。"""
    narrowed = dataclasses.replace(
        get_adapter(runtime), execution=ExecutionContract(keys=dict(keys))
    )
    monkeypatch.setattr(
        catalog,
        "_ADAPTERS",
        tuple(narrowed if adapter.name == runtime else adapter for adapter in catalog._ADAPTERS),
    )


@pytest.mark.parametrize("runtime", sorted(CONTRACT_TABLE))
def test_adapter_declares_contract(runtime: str) -> None:
    contract = get_adapter(runtime).execution
    expected = CONTRACT_TABLE[runtime]
    assert set(contract.keys) == set(expected)
    for key, required in expected.items():
        assert contract.keys[key].required is required


@pytest.mark.parametrize("runtime", ["pi", "velites"])
def test_required_provider_missing_fails(runtime: str) -> None:
    with pytest.raises(ValueError, match="node gen requires a provider"):
        resolve_execution(_node(model="m"), runtime)


@pytest.mark.parametrize("runtime", ["pi", "velites"])
def test_required_model_missing_fails(runtime: str) -> None:
    with pytest.raises(ValueError, match="node gen requires a model"):
        resolve_execution(_node(provider="p"), runtime)


@pytest.mark.parametrize("runtime", ["pi", "velites"])
def test_optional_thinking_empty_passes(runtime: str) -> None:
    block = resolve_execution(_node(provider="p", model="m"), runtime)
    assert block["thinking"] == ""
    assert block["binary"] == runtime


def test_openclaw_contract_provider_optional_model_required() -> None:
    # 阶段 3 起 openclaw 已接入：provider 可选（拼 model 串），model 必填。
    block = resolve_execution(_node(provider="", model="m"), "openclaw")
    assert block["binary"] == "openclaw"
    assert block["provider"] == ""
    assert block["thinking"] == ""
    with pytest.raises(ValueError, match="node gen requires a model"):
        resolve_execution(_node(provider="p"), "openclaw")


def test_unsupported_key_configured_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    _narrow_contract(
        monkeypatch,
        "velites",
        {
            "provider": ExecutionKeyRule(required=True, semantics=""),
            "model": ExecutionKeyRule(required=True, semantics=""),
        },
    )
    with pytest.raises(ValueError, match=r"node gen configures execution\.thinking.*'velites'"):
        validate_execution_contract(
            node_key="gen",
            runtime="velites",
            values={"provider": "p", "model": "m", "thinking": "high"},
        )


def test_unsupported_key_empty_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _narrow_contract(
        monkeypatch,
        "velites",
        {
            "provider": ExecutionKeyRule(required=True, semantics=""),
            "model": ExecutionKeyRule(required=True, semantics=""),
        },
    )
    resolved = validate_execution_contract(
        node_key="gen",
        runtime="velites",
        values={"provider": "p", "model": "m", "thinking": ""},
    )
    assert resolved == {"provider": "p", "model": "m", "thinking": ""}


def test_unknown_runtime_fails_fast_listing_catalog() -> None:
    with pytest.raises(ValueError, match=r"unknown agent runtime 'rust'.*pi, openclaw, velites"):
        validate_execution_contract(node_key="gen", runtime="rust", values={})
