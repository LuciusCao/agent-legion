"""Runtime 工具目录一致性（#476，纯静态 + 跨二进制对账）。

钉住三层全等：Host 侧 adapter 静态声明 ↔ ``velites tools list --json``
实际输出（binary 缺失且无法 cargo build 时跳过对账、保留静态断言）；
以及 dispatch 校验（#449）与 API 投影都从同一 catalog 取数。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.app.agent_runtime.adapter import RuntimeAdapter
from server.app.agent_runtime.catalog import AGENT_RUNTIMES, get_adapter, get_tool_catalog
from server.app.agent_runtime.tool_catalog import (
    PI_TOOL_CATALOG,
    VELITES_TOOL_CATALOG,
    default_tool_names,
    selectable_tool_names,
)
from server.app.agent_runtime.tools_validation import manifest_tools, validate_tools
from server.app.services.agent_runtime_catalog import AgentRuntimeCatalogService

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_BIN = REPO_ROOT / "velites" / "target" / "debug" / "velites"


def _entry_dict(entry) -> dict:
    # activation=None 与「省略该键」在对账语义上等价（serde 侧
    # skip_serializing_if），静态镜像用 None、JSON 里没有该键。
    payload = {
        "name": entry.name,
        "tier": entry.tier,
        "description": entry.description,
        "parameters": entry.parameters,
    }
    if entry.activation:
        payload["activation"] = entry.activation
    return payload


def test_every_adapter_declares_a_tool_catalog() -> None:
    for runtime in AGENT_RUNTIMES:
        adapter: RuntimeAdapter = get_adapter(runtime)
        assert adapter.tool_catalog, f"{runtime} must declare a tool catalog (#476)"


def test_velites_static_catalog_shape() -> None:
    """静态断言（不依赖二进制）：tier 分档与 AgentDefinition 默认值对齐。"""
    tiers = {entry.name: entry.tier for entry in VELITES_TOOL_CATALOG}
    assert tiers == {
        "read": "default",
        "write": "default",
        "bash": "default",
        "uuid": "opt-in",
        "json": "opt-in",
        "validate": "forced",
    }
    validate = next(e for e in VELITES_TOOL_CATALOG if e.name == "validate")
    assert validate.activation == "--require-output"
    # #476 拍板：默认集 = AgentDefinition.tools 的三件套默认值。
    assert default_tool_names(VELITES_TOOL_CATALOG) == ("read", "write", "bash")


def test_pi_static_catalog_is_the_triple() -> None:
    """pi 静态登记（外部 runtime 无自描述通道）。"""
    assert [entry.name for entry in PI_TOOL_CATALOG] == ["read", "write", "bash"]
    assert all(entry.tier == "default" for entry in PI_TOOL_CATALOG)


def test_forced_tier_is_excluded_from_selectable_set() -> None:
    selectable = selectable_tool_names(VELITES_TOOL_CATALOG)
    assert "validate" not in selectable
    assert selectable == {"read", "write", "bash", "uuid", "json"}


@pytest.mark.skipif(
    not VELITES_BIN.exists() and not shutil.which("cargo"),
    reason="no velites binary and no cargo to build one",
)
def test_velites_static_catalog_matches_tools_list_json() -> None:
    """跨二进制对账（#476 核心）：静态声明与 `velites tools list --json` 全等。

    binary 不在时先 cargo build（同 tests/executors 的 controllability 模式）。
    version 字段随 Cargo.toml 走，不参与对账（host 目录不钉 harness 版本）。
    """
    binary = VELITES_BIN
    if not binary.exists():
        subprocess.run(
            ["cargo", "build"],
            cwd=REPO_ROOT / "velites",
            check=True,
            capture_output=True,
        )
    output = subprocess.run(
        [str(binary), "tools", "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    payload = json.loads(output)
    declared = [_entry_dict(entry) for entry in VELITES_TOOL_CATALOG]
    assert payload["tools"] == declared, (
        "server/app/agent_runtime/tool_catalog.py drifted from "
        "`velites tools list --json`; regenerate the static mirror"
    )


class TestValidateTools:
    def test_unknown_tool_fails_fast_with_actionable_message(self) -> None:
        with pytest.raises(ValueError, match=r"not offered by agent runtime 'velites'.*uuid2"):
            validate_tools(node_key="n1", runtime="velites", tools=["read", "uuid2"])

    def test_forced_tool_is_silently_dropped(self) -> None:
        # --tools validate 向后兼容（velites 侧 no-op，host 侧剔除）。
        assert validate_tools(node_key="n1", runtime="velites", tools=["read", "validate"]) == [
            "read"
        ]

    def test_per_runtime_heterogeneity(self) -> None:
        with pytest.raises(ValueError, match="uuid"):
            validate_tools(node_key="n1", runtime="pi", tools=["uuid"])
        assert validate_tools(node_key="n1", runtime="pi", tools=["read"]) == ["read"]

    def test_order_is_normalized_to_catalog_order(self) -> None:
        assert validate_tools(node_key="n1", runtime="velites", tools=["uuid", "bash", "read"]) == [
            "read",
            "bash",
            "uuid",
        ]

    def test_empty_selection_is_legal(self) -> None:
        assert validate_tools(node_key="n1", runtime="velites", tools=[]) == []


class TestManifestTools:
    def test_node_declaration_wins_over_definition_fallback(self) -> None:
        assert manifest_tools(
            node_key="n1",
            runtime="velites",
            node_tools=("uuid",),
            fallback=("read", "write", "bash"),
        ) == ["uuid"]

    def test_empty_node_declaration_falls_back(self) -> None:
        assert manifest_tools(
            node_key="n1",
            runtime="velites",
            node_tools=(),
            fallback=("read", "write", "bash"),
        ) == ["read", "write", "bash"]

    def test_fallback_unknown_tool_also_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="not offered"):
            manifest_tools(node_key="n1", runtime="pi", node_tools=(), fallback=("uuid",))


class TestCatalogService:
    def test_projection_is_nested_per_runtime(self) -> None:
        out = AgentRuntimeCatalogService().tool_catalog()
        assert set(out["runtimes"]) == set(AGENT_RUNTIMES)
        velites = {tool["name"]: tool for tool in out["runtimes"]["velites"]["tools"]}
        assert velites["validate"]["tier"] == "forced"
        assert velites["validate"]["activation"] == "--require-output"
        assert "activation" not in velites["read"]
        pi_names = [tool["name"] for tool in out["runtimes"]["pi"]["tools"]]
        assert pi_names == ["read", "write", "bash"]

    def test_get_tool_catalog_matches_adapter_declaration(self) -> None:
        for runtime in AGENT_RUNTIMES:
            assert get_tool_catalog(runtime) == get_adapter(runtime).tool_catalog
