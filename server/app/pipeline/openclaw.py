import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from server.app.pipeline.agent_workspace import cleanup_agent_workspace_files

logger = logging.getLogger(__name__)


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


@dataclass
class SkillSafetyConfig:
    enabled: bool
    repos: list[dict[str, str]]


def restore_skill_repos(repos: list[dict[str, str]]) -> None:
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for repo in repos:
        path = Path(repo["path"]).expanduser().resolve()
        ref = repo["ref"]
        if not (path / ".git").is_dir():
            logger.warning("Skill safety: %s is not a git repo, skipping", path)
            continue
        checkout = subprocess.run(
            ["git", "-C", str(path), "checkout", ref, "-f"],
            capture_output=True,
            env=clean_env,
            text=True,
        )
        if checkout.returncode != 0:
            logger.error(
                "Skill safety: failed to checkout %s in %s: %s",
                ref,
                path,
                checkout.stderr,
            )
            continue
        clean = subprocess.run(
            ["git", "-C", str(path), "clean", "-fd"],
            capture_output=True,
            env=clean_env,
            text=True,
        )
        if clean.returncode != 0:
            logger.warning(
                "Skill safety: failed to clean %s: %s",
                path,
                clean.stderr,
            )
        logger.info("Skill safety: restored %s to %s", path, ref)


def extract_openclaw_arg(command: list[str], name: str) -> str:
    for i, part in enumerate(command):
        if part == name and i + 1 < len(command):
            return command[i + 1]
        prefix = f"{name}="
        if part.startswith(prefix):
            return part[len(prefix) :]
    return ""


class OpenClawRunner:
    def __init__(
        self,
        command_template: list[str],
        cwd: Path,
        timeout_seconds: int,
        skill_safety: SkillSafetyConfig | None = None,
        isolated_workspace_root: Path | None = None,
    ):
        self.command_template = command_template
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.agent_id = self._extract_agent_id(command_template)
        self.skill_safety = skill_safety
        self.isolated_workspace_root = isolated_workspace_root

    @staticmethod
    def _extract_agent_id(command_template: list[str]) -> str:
        for i, part in enumerate(command_template):
            if part == "--agent" and i + 1 < len(command_template):
                return command_template[i + 1]
        return ""

    _SHELL_METACHARACTERS = re.compile(r"[;|&$`\\(){}<>'\"?\[\]*\n]")

    @staticmethod
    def _sanitize_replacement(value: str) -> str:
        """Remove null bytes and escape shell metacharacters from replacement strings.

        Only values containing actual shell metacharacters are quoted, so normal
        prompt text remains unchanged.
        """
        value = value.replace("\x00", "")
        if OpenClawRunner._SHELL_METACHARACTERS.search(value):
            return shlex.quote(value)
        return value

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
        if self.skill_safety is not None and self.skill_safety.enabled:
            restore_skill_repos(self.skill_safety.repos)

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
        run_cwd = self.cwd
        isolated_cwd: Path | None = None
        if self.isolated_workspace_root is not None:
            safe_video_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", video_id)
            safe_phase = re.sub(r"[^A-Za-z0-9_.-]+", "_", phase.key)
            isolated_cwd = self.isolated_workspace_root / f"{safe_video_id}-{safe_phase}"
            isolated_cwd.mkdir(parents=True, exist_ok=True)
            run_cwd = isolated_cwd

        with log_path.open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(
                    command,
                    cwd=run_cwd,
                    shell=False,
                    text=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                cleanup_agent_workspace_files(video_dir)
                if isolated_cwd is not None:
                    shutil.rmtree(isolated_cwd, ignore_errors=True)
                return AgentRunResult("failed", -1, command, "openclaw command timed out")
            finally:
                cleanup_agent_workspace_files(video_dir)
                if isolated_cwd is not None:
                    shutil.rmtree(isolated_cwd, ignore_errors=True)

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
