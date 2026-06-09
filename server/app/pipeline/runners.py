import json
import subprocess
from pathlib import Path
from typing import Any

from server.app.pipeline.openclaw import OpenClawRunner, SkillSafetyConfig
from server.app.settings import Settings


def list_openclaw_agents(timeout: int = 10) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["openclaw", "agents", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return [a for a in data if isinstance(a, dict) and "id" in a]
    except Exception:
        return []


def discover_openclaw_agents(timeout: int = 10) -> list[str]:
    return [a["id"] for a in list_openclaw_agents(timeout)]


def _build_agent_command(base_template: list[str], agent_id: str) -> list[str]:
    result: list[str] = []
    i = 0
    while i < len(base_template):
        part = base_template[i]
        if part == "--agent" and i + 1 < len(base_template):
            result.extend(["--agent", agent_id])
            i += 2
        else:
            result.append(part)
            i += 1
    return result


def build_openclaw_runners(
    settings: Settings, discovered_agent_ids: list[str] | None = None
) -> list[OpenClawRunner]:
    openclaw = settings.config.get("openclaw", {})
    base_cwd = (settings.root_dir / str(openclaw.get("cwd", "."))).resolve()
    timeout_seconds = int(openclaw.get("timeout_seconds", 600))
    workspace_root_raw = openclaw.get("workspace_dir", settings.data_dir / "openclaw-workspaces")
    workspace_root = Path(workspace_root_raw)
    if not workspace_root.is_absolute():
        workspace_root = (settings.root_dir / workspace_root).resolve()

    skill_safety: SkillSafetyConfig | None = None
    skill_safety_raw = openclaw.get("skill_safety")
    if skill_safety_raw is not None:
        skill_safety = SkillSafetyConfig(
            enabled=bool(skill_safety_raw.get("enabled", True)),
            repos=list(skill_safety_raw.get("repos", [])),
        )

    runners_config = openclaw.get("runners")
    if runners_config:
        runners: list[OpenClawRunner] = []
        for r in runners_config:
            count = int(r.get("count", 1))
            template = list(r["command_template"])
            for _ in range(count):
                runners.append(
                    OpenClawRunner(
                        command_template=list(template),
                        cwd=base_cwd,
                        timeout_seconds=timeout_seconds,
                        skill_safety=skill_safety,
                        isolated_workspace_root=workspace_root,
                    )
                )
        return runners

    base_template = list(
        openclaw.get(
            "command_template",
            [
                "openclaw",
                "agent",
                "--local",
                "--agent",
                "main",
                "--message",
                "{prompt_text}",
                "--json",
            ],
        )
    )

    if "--agent" in base_template:
        agents = (
            discovered_agent_ids if discovered_agent_ids is not None else discover_openclaw_agents()
        )
        if agents:
            return [
                OpenClawRunner(
                    command_template=_build_agent_command(base_template, agent_id),
                    cwd=base_cwd,
                    timeout_seconds=timeout_seconds,
                    skill_safety=skill_safety,
                    isolated_workspace_root=workspace_root,
                )
                for agent_id in agents
            ]

    return [
        OpenClawRunner(
            command_template=base_template,
            cwd=base_cwd,
            timeout_seconds=timeout_seconds,
            skill_safety=skill_safety,
            isolated_workspace_root=workspace_root,
        )
    ]


def build_openclaw_runner(settings: Settings) -> OpenClawRunner:
    return build_openclaw_runners(settings)[0]


class RunnerPool:
    def __init__(
        self,
        runners: list[OpenClawRunner] | None = None,
        agent_manager: Any = None,
    ) -> None:
        self._runners: list[OpenClawRunner] = list(runners) if runners is not None else []
        self._busy_indices: set[int] = set()
        self._agent_manager = agent_manager

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        discovered_agent_ids: list[str] | None = None,
        agent_manager: Any = None,
    ) -> "RunnerPool":
        return cls(
            build_openclaw_runners(settings, discovered_agent_ids), agent_manager=agent_manager
        )

    def size(self) -> int:
        return len(self._runners)

    def all_runners(self) -> list[OpenClawRunner]:
        return list(self._runners)

    def acquire(self, workspace_id: str | None = None) -> tuple[int, OpenClawRunner]:
        if not self._runners:
            raise RuntimeError("Runners not initialized.")
        allowed: set[str] | None = None
        if workspace_id and self._agent_manager is not None:
            allowed = set(self._agent_manager.get_allowed_agents(workspace_id))
        for i, runner in enumerate(self._runners):
            if i in self._busy_indices:
                continue
            agent_id = getattr(runner, "agent_id", None) or f"runner-{i}"
            if allowed is not None and agent_id not in allowed:
                continue
            self._busy_indices.add(i)
            return i, runner
        raise RuntimeError("No free runner available")

    def release(self, index: int) -> None:
        self._busy_indices.discard(index)
