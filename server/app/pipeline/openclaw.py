import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentPhase:
    key: str
    reference_path: Path
    expected_outputs: list[str]
    json_outputs: list[str]


@dataclass
class AgentRunResult:
    status: str
    exit_code: int
    command: list[str]
    error_message: str = ""


def extract_openclaw_arg(command: list[str], name: str) -> str:
    for i, part in enumerate(command):
        if part == name and i + 1 < len(command):
            return command[i + 1]
        prefix = f"{name}="
        if part.startswith(prefix):
            return part[len(prefix) :]
    return ""


class OpenClawRunner:
    def __init__(self, command_template: list[str], cwd: Path, timeout_seconds: int):
        self.command_template = command_template
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.agent_id = self._extract_agent_id(command_template)

    @staticmethod
    def _extract_agent_id(command_template: list[str]) -> str:
        for i, part in enumerate(command_template):
            if part == "--agent" and i + 1 < len(command_template):
                return command_template[i + 1]
        return ""

    @staticmethod
    def _sanitize_replacement(value: str) -> str:
        """Remove null bytes from replacement strings to prevent argument injection via embedded NUL terminators."""
        return value.replace("\x00", "")

    def render_command(self, video_id: str, video_dir: Path, prompt_file: Path) -> list[str]:
        import time

        prompt_text = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
        replacements = {
            "{video_id}": video_id,
            "{video_dir}": str(video_dir),
            "{prompt_file}": str(prompt_file),
            "{prompt_text}": self._sanitize_replacement(prompt_text),
            "{timestamp}": str(int(time.time())),
        }
        rendered = []
        for part in self.command_template:
            for token, value in replacements.items():
                part = part.replace(token, value)
            rendered.append(part)
        return rendered

    def run(
        self,
        phase: AgentPhase,
        video_id: str,
        video_dir: Path,
        prompt_dir: Path,
        log_path: Path,
    ) -> AgentRunResult:
        video_dir.mkdir(parents=True, exist_ok=True)
        prompt_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        reference = (
            phase.reference_path.read_text(encoding="utf-8")
            if phase.reference_path.exists()
            else ""
        )
        prompt_file = prompt_dir / f"{video_id}-{phase.key}.md"
        prompt_file.write_text(
            f"{reference}\n\nVideo ID: {video_id}\nVideo directory: {video_dir}\n",
            encoding="utf-8",
        )
        command = self.render_command(video_id, video_dir, prompt_file)

        with log_path.open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(
                    command,
                    cwd=self.cwd,
                    shell=False,
                    text=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return AgentRunResult("failed", -1, command, "openclaw command timed out")

        if completed.returncode != 0:
            return AgentRunResult(
                "failed", completed.returncode, command, "openclaw command failed"
            )

        missing = [name for name in phase.expected_outputs if not (video_dir / name).exists()]
        if missing:
            return AgentRunResult(
                "failed", completed.returncode, command, f"missing outputs: {', '.join(missing)}"
            )

        for name in phase.json_outputs:
            try:
                json.loads((video_dir / name).read_text(encoding="utf-8"))
            except Exception as exc:
                return AgentRunResult(
                    "failed", completed.returncode, command, f"invalid json {name}: {exc}"
                )

        return AgentRunResult("completed", completed.returncode, command)
