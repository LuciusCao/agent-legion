"""agent_catalog：Agent 列表来自传入 catalog，不再投影全局 provider/model/thinking。

全局 ``workflows.pi`` 投影已随 YAML 退役（agent 配置治理 phase 3）：执行默认
是 workflow 级配置（顶层 ``execution`` 块，schema v64 起 workspace
agentDefaults 也已退役），全局 catalog 无从投影，前端「继承默认」提示改读
草稿 YAML 的顶层 execution 块。P-0.5（schema v47）后 catalog
只剩 Agent 半边：executor 概念整体退役。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_catalog_projection import agent_catalog
from server.app.settings import Settings


class _StubSkills:
    def metadata(self, skill: str) -> dict[str, Any]:
        return {}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )


_AGENTS = {
    "agent-pi": AgentDefinition(capability="cap-pi", runtime="pi", skill="q/a"),
    "agent-velites": AgentDefinition(capability="cap-v", runtime="velites", skill="q/b"),
    "agent-openclaw": AgentDefinition(capability="cap-o", runtime="openclaw", skill="q/c"),
}


@pytest.mark.no_db
def test_catalog_lists_agents_without_execution_projection(tmp_path: Path) -> None:
    catalog = agent_catalog(_settings(tmp_path), _StubSkills(), "ws-test", _AGENTS)

    agents = {entry["id"]: entry for entry in catalog["agents"]}
    assert set(agents) == set(_AGENTS)
    for entry in agents.values():
        # 执行配置（provider/model/thinking）由 workspace 默认 + 节点覆盖解析，
        # 不属于全局 catalog 投影。
        assert "provider" not in entry
        assert "model" not in entry
        assert "thinking" not in entry
    assert agents["agent-velites"]["runtime"] == "velites"
    assert agents["agent-velites"]["capability"] == "cap-v"
    assert agents["agent-velites"]["skill"] == "q/b"


@pytest.mark.no_db
def test_catalog_has_no_executors_half(tmp_path: Path) -> None:
    """P-0.5（schema v47）：executor 概念退役，catalog 只剩 Agent 半边。"""
    catalog = agent_catalog(_settings(tmp_path), _StubSkills(), "ws-test", _AGENTS)

    assert set(catalog) == {"agents"}
