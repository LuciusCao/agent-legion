import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.executors.agent_workspace import cleanup_agent_workspace_files
from server.app.executors.cancellation import CancellationToken, SubprocessTracker
from server.app.executors.models import ExecutionStatus
from server.app.skills.config import LockedSkillSource, SkillsLock
from server.app.skills.errors import SkillConfigError

logger = logging.getLogger(__name__)


def _lock_repo_path(repo: str) -> str | None:
    """Normalize a locked skill repo location to a local filesystem path."""
    if repo.startswith("file://"):
        repo = repo[len("file://") :]
    path = Path(repo).expanduser()
    if not path.is_absolute():
        return None
    return str(path.resolve())


def resolve_skill_safety_repos(paths: list[str], lock: SkillsLock) -> list[dict[str, str]]:
    """Resolve skill-safety whitelist paths to locked refs from ``skills.lock``.

    The whitelist only declares which checkouts may be force-restored; the
    restore ref is the locked commit (falling back to the locked ref), so the
    runner can never checkout a ref that diverges from the skill manager pin.
    """
    locked_by_path: dict[str, LockedSkillSource] = {}
    for source in lock.skills.values():
        repo_path = _lock_repo_path(source.repo)
        if repo_path is not None:
            locked_by_path.setdefault(repo_path, source)
    resolved: list[dict[str, str]] = []
    for raw_path in paths:
        path = str(Path(raw_path).expanduser().resolve())
        locked = locked_by_path.get(path)
        if locked is None:
            raise SkillConfigError(
                f"openclaw skill_safety repo {raw_path!r} is not declared in skills.lock; "
                "declare the skill in config/skills.yaml and refresh the lock, or remove "
                "the whitelist entry"
            )
        resolved.append({"path": path, "ref": locked.commit or locked.ref})
    return resolved


@dataclass
class AgentPhase:
    key: str
    reference_path: Path
    expected_outputs: list[str]
    json_outputs: list[str]


@dataclass
class AgentRunResult:
    status: ExecutionStatus
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
        agent_id: str | None = None,
        cancellation_grace_seconds: int = 5,
    ):
        self.command_template = command_template
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self.agent_id = agent_id or self._extract_agent_id(command_template)
        self.skill_safety = skill_safety
        self.isolated_workspace_root = isolated_workspace_root
        self._tracker = SubprocessTracker(grace_seconds=cancellation_grace_seconds)

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

    def run_prompt(
        self,
        *,
        execution_id: str,
        work_dir: Path,
        prompt_text: str,
        expected_outputs: tuple[str, ...] | list[str],
        log_path: Path,
        json_outputs: tuple[str, ...] | list[str] | None = None,
        cancellation_token: CancellationToken | None = None,
        tracker: SubprocessTracker | None = None,
    ) -> AgentRunResult:
        """Run one Workspace prompt through this configured OpenClaw agent."""
        if self.skill_safety is not None and self.skill_safety.enabled:
            restore_skill_repos(self.skill_safety.repos)

        work_dir.mkdir(parents=True, exist_ok=True)
        prompt_dir = work_dir / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        prompt_file = prompt_dir / f"{execution_id}.md"
        prompt_file.write_text(prompt_text, encoding="utf-8")
        command = self.render_command(execution_id, work_dir, prompt_file)
        run_cwd = self.cwd
        isolated_cwd: Path | None = None
        if self.isolated_workspace_root is not None:
            safe_execution_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", execution_id)
            isolated_cwd = self.isolated_workspace_root / safe_execution_id
            isolated_cwd.mkdir(parents=True, exist_ok=True)
            run_cwd = isolated_cwd

        effective_tracker = tracker or self._tracker
        with log_path.open("w", encoding="utf-8") as log:
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=run_cwd,
                    shell=False,
                    text=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                effective_tracker.register(execution_id, proc)
                try:
                    exit_code, error_message = self._wait_for_process(
                        proc,
                        command=command,
                        cancellation_token=cancellation_token,
                        tracker=effective_tracker,
                        execution_id=execution_id,
                    )
                finally:
                    effective_tracker.unregister(execution_id)
            except FileNotFoundError:
                cleanup_agent_workspace_files(work_dir)
                if isolated_cwd is not None:
                    shutil.rmtree(isolated_cwd, ignore_errors=True)
                return AgentRunResult("failed", 127, command, "openclaw binary not found")
            except subprocess.TimeoutExpired:
                cleanup_agent_workspace_files(work_dir)
                if isolated_cwd is not None:
                    shutil.rmtree(isolated_cwd, ignore_errors=True)
                return AgentRunResult("failed", -1, command, "openclaw command timed out")
            finally:
                cleanup_agent_workspace_files(work_dir)
                if isolated_cwd is not None:
                    shutil.rmtree(isolated_cwd, ignore_errors=True)

        if exit_code != 0:
            return AgentRunResult(
                "failed", exit_code, command, error_message or "openclaw command failed"
            )

        missing = [name for name in expected_outputs if not (work_dir / name).exists()]
        if missing:
            return AgentRunResult(
                "failed", exit_code, command, f"missing outputs: {', '.join(missing)}"
            )

        json_outputs = json_outputs or ()
        for name in json_outputs:
            try:
                json.loads((work_dir / name).read_text(encoding="utf-8"))
            except Exception as exc:
                return AgentRunResult("failed", exit_code, command, f"invalid json {name}: {exc}")

        return AgentRunResult("completed", exit_code, command)

    def _wait_for_process(
        self,
        proc: subprocess.Popen[Any],
        *,
        command: list[str],
        cancellation_token: CancellationToken | None,
        tracker: SubprocessTracker,
        execution_id: str,
    ) -> tuple[int, str]:
        start = time.monotonic()
        while True:
            if cancellation_token is not None and cancellation_token.wait(timeout=0.05):
                tracker.cancel(execution_id)
                return self._collect_exit(proc, cancelled=True)
            poll = proc.poll()
            if poll is not None:
                return poll, ""
            if time.monotonic() - start > self.timeout_seconds:
                tracker.cancel(execution_id)
                return -1, "openclaw command timed out"

    def _collect_exit(
        self, proc: subprocess.Popen[Any], cancelled: bool = False
    ) -> tuple[int, str]:
        try:
            exit_code = proc.wait(timeout=self.cancellation_grace_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            exit_code = -1
        return exit_code, "execution was cancelled" if cancelled else ""

    def cancel(self, execution_id: str) -> None:
        """Public cancellation hook used by executor adapters."""
        self._tracker.cancel(execution_id)

    def run(
        self,
        phase: AgentPhase,
        video_id: str,
        video_dir: Path,
        prompt_dir: Path,
        log_path: Path,
    ) -> AgentRunResult:
        reference = (
            phase.reference_path.read_text(encoding="utf-8")
            if phase.reference_path.exists()
            else ""
        )
        prompt_text = f"{reference}\n\nVideo ID: {video_id}\nVideo directory: {video_dir}\n"
        return self.run_prompt(
            execution_id=f"{video_id}-{phase.key}",
            work_dir=video_dir,
            prompt_text=prompt_text,
            expected_outputs=phase.expected_outputs,
            log_path=log_path,
            json_outputs=phase.json_outputs,
        )
