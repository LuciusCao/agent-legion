"""execution_catalog_projection：provider/model/thinking 按 runtime 投影。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.services.execution_catalog_projection import execution_catalog
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
        agent_definitions={
            "agent-pi": AgentDefinition(capability="cap-pi", runtime="pi", skill="q/a"),
            "agent-velites": AgentDefinition(capability="cap-v", runtime="velites", skill="q/b"),
            "agent-openclaw": AgentDefinition(capability="cap-o", runtime="openclaw", skill="q/c"),
        },
        executor_runtime=ExecutorRuntimeConfig.model_validate(
            {
                "workflows": {
                    "enabled": True,
                    "pi": {"provider": "gateway", "model": "m1", "thinking": "high"},
                },
                "openclaw": {"command_template": ["openclaw"]},
            }
        ),
    )


@pytest.mark.no_db
def test_projection_includes_pi_and_velites_but_not_openclaw(tmp_path: Path) -> None:
    catalog = execution_catalog(_settings(tmp_path), _StubSkills())

    agents = {entry["id"]: entry for entry in catalog["agents"]}
    # velites 同样需要 provider/model/thinking 投影（manifest 冻结同源）。
    for agent_id in ("agent-pi", "agent-velites"):
        assert agents[agent_id]["provider"] == "gateway"
        assert agents[agent_id]["model"] == "m1"
        assert agents[agent_id]["thinking"] == "high"
    assert "provider" not in agents["agent-openclaw"]
    assert "model" not in agents["agent-openclaw"]
    assert "thinking" not in agents["agent-openclaw"]
