import json
import subprocess
from typing import Any

from server.app.pipeline.openclaw import OpenClawRunner
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

    runners_config = openclaw.get("runners")
    if runners_config:
        return [
            OpenClawRunner(
                command_template=list(r["command_template"]),
                cwd=base_cwd,
                timeout_seconds=timeout_seconds,
            )
            for r in runners_config
        ]

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
            discovered_agent_ids
            if discovered_agent_ids is not None
            else discover_openclaw_agents()
        )
        if agents:
            return [
                OpenClawRunner(
                    command_template=_build_agent_command(base_template, agent_id),
                    cwd=base_cwd,
                    timeout_seconds=timeout_seconds,
                )
                for agent_id in agents
            ]

    return [
        OpenClawRunner(
            command_template=base_template,
            cwd=base_cwd,
            timeout_seconds=timeout_seconds,
        )
    ]


def build_openclaw_runner(settings: Settings) -> OpenClawRunner:
    return build_openclaw_runners(settings)[0]


class RunnerPool:
    def __init__(self, runners: list[OpenClawRunner] | None = None) -> None:
        self._runners: list[OpenClawRunner] = list(runners) if runners is not None else []
        self._busy_indices: set[int] = set()

    @classmethod
    def from_settings(
        cls, settings: Settings, discovered_agent_ids: list[str] | None = None
    ) -> "RunnerPool":
        return cls(build_openclaw_runners(settings, discovered_agent_ids))

    def size(self) -> int:
        return len(self._runners)

    def all_runners(self) -> list[OpenClawRunner]:
        return list(self._runners)

    def acquire(self) -> tuple[int, OpenClawRunner]:
        if not self._runners:
            raise RuntimeError("Runners not initialized.")
        for i, runner in enumerate(self._runners):
            if i not in self._busy_indices:
                self._busy_indices.add(i)
                return i, runner
        raise RuntimeError("No free runner available")

    def release(self, index: int) -> None:
        self._busy_indices.discard(index)
