from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from server.app.executors.cancellation import CancellationToken, SubprocessTracker
from server.app.executors.models import ExecutionStatus
from server.app.jobs import JobQueries
from server.app.services.run_dir_cleanup import cleanup_extra_runs_for_node
from server.app.storage_paths import ManagedPathError, make_data_relative, resolve_job_dir
from server.app.workflows.pi_command_builder import build_pi_command
from server.app.workflows.pi_config import PiConfig, PiRunResult

logger = logging.getLogger(__name__)


class PiRunner:
    def __init__(self, config: PiConfig, skill_root: Path):
        self.config = config
        self.skill_root = skill_root

    @classmethod
    def from_config(cls, raw: dict[str, Any], skill_root: Path) -> PiRunner:
        return cls(PiConfig.from_config(raw), skill_root)

    def run(
        self,
        *,
        job: dict[str, Any],
        node_key: str,
        skill_dir: Path,
        inputs: list[str],
        outputs: list[str],
        tools: list[str] | None = None,
        job_db: JobQueries | None = None,
        persist_run: bool = True,
        job_dir: Path | None = None,
        jobs_dir: Path | None = None,
        execution_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
        tracker: SubprocessTracker | None = None,
        skill_version: str = "",
    ) -> PiRunResult:
        if job_dir is None:
            if jobs_dir is None:
                raise ManagedPathError(
                    "jobs_dir managed root is required",
                    record_id=str(job["id"]),
                    root_kind="job",
                )
            job_dir = resolve_job_dir(job, jobs_dir)
        data_dir = job_db.jobs_dir.parent if job_db is not None else None
        run_token = str(uuid.uuid4())
        run_dir = job_dir / "runs" / node_key / run_token
        session_dir = run_dir / "session"
        run_dir.mkdir(parents=True, exist_ok=True)
        session_dir.mkdir(parents=True, exist_ok=True)

        prompt_file = run_dir / "prompt.md"
        events_file = run_dir / "events.jsonl"
        stderr_file = run_dir / "stderr.log"

        prompt = self._build_prompt(
            job_id=job["id"],
            node_key=node_key,
            job_dir=job_dir,
            skill_dir=skill_dir,
            validator_script=skill_dir / "scripts" / "validate_output.py",
            inputs=inputs,
            outputs=outputs,
        )
        prompt_file.write_text(prompt, encoding="utf-8")

        session_name = f"{job['id']}:{node_key}:{run_token}"
        command = build_pi_command(
            self.config,
            skill_dir=skill_dir,
            session_dir=session_dir,
            tools=tools or ["read", "write", "bash"],
            session_name=session_name,
            prompt_file=prompt_file,
        )

        run_record: dict[str, Any] | None = None
        start_time = datetime.datetime.now(datetime.UTC).isoformat()
        exit_code = 0
        error_message = ""

        try:
            if job_db is not None and persist_run:
                data_dir = job_db.jobs_dir.parent
                run_record = job_db.start_node_run(
                    job["id"],
                    node_key,
                    command,
                    make_data_relative(events_file, data_dir),
                    run_dir=make_data_relative(run_dir, data_dir),
                    session_dir=make_data_relative(session_dir, data_dir),
                    skill_version=skill_version,
                )
                if run_record is None:
                    return PiRunResult(
                        status="cancelled",
                        exit_code=-1,
                        command=command,
                        run_dir=run_dir,
                        session_dir=session_dir,
                        error_message="node run not in a startable state",
                    )

            env = dict(os.environ)
            env.update(self.config.environment)

            with (
                open(events_file, "w") as stdout_fh,
                open(stderr_file, "w") as stderr_fh,
            ):
                proc = subprocess.Popen(
                    command,
                    stdout=stdout_fh,
                    stderr=stderr_fh,
                    cwd=str(job_dir),
                    env=env,
                    start_new_session=True,
                )
                effective_execution_id = execution_id or run_token
                if tracker is not None:
                    tracker.register(effective_execution_id, proc)
                try:
                    exit_code, error_message = self._wait_for_process(
                        proc,
                        cancellation_token=cancellation_token,
                        tracker=tracker,
                        execution_id=effective_execution_id,
                    )
                finally:
                    if tracker is not None:
                        tracker.unregister(effective_execution_id)
        except FileNotFoundError:
            exit_code = 127
            error_message = f"Pi binary not found: {self.config.binary}"
        except Exception as exc:
            exit_code = 1
            error_message = str(exc)

        # Validate outputs
        if exit_code == 0:
            model_error = self._detect_model_error(events_file)
            if model_error:
                exit_code = 1
                error_message = f"Pi model call failed: {model_error}"
            else:
                missing = [o for o in outputs if not (job_dir / o).is_file()]
                if missing:
                    exit_code = 1
                    error_message = f"Missing outputs after Pi run: {', '.join(missing)}"

        if exit_code == 0:
            validator = skill_dir / "scripts" / "validate_output.py"
            if validator.is_file():
                try:
                    val_proc = subprocess.run(
                        [sys.executable, str(validator), str(job_dir)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if val_proc.returncode != 0:
                        exit_code = 1
                        error_message = f"Output validation failed: {val_proc.stderr.strip()}"
                except Exception as exc:
                    exit_code = 1
                    error_message = f"Validator error: {exc}"

        end_time = datetime.datetime.now(datetime.UTC).isoformat()
        run_meta = {
            "node_key": node_key,
            "run_id": run_token,
            "command": command,
            "start_time": start_time,
            "end_time": end_time,
            "exit_code": exit_code,
            "model": {
                "provider": self.config.provider,
                "model": self.config.model,
                "thinking": self.config.thinking,
            },
            "inputs": inputs,
            "outputs": outputs,
            "skill": str(skill_dir),
            "skill_version": skill_version,
            "error_message": error_message,
        }
        (run_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2))

        status: ExecutionStatus = "completed" if exit_code == 0 else "failed"
        if job_db is not None and persist_run and run_record is not None:
            job_db.finish_node_run(run_record["id"], status, exit_code, error_message)

        # This run is now the latest for this node; remove any older run dirs.
        if data_dir is not None and job_db is not None:
            try:
                with job_db.connect() as conn:
                    cleanup_extra_runs_for_node(conn, data_dir, job_dir, node_key)
            except Exception:
                logger.exception(
                    "Failed to clean up old run dirs for %s/%s", job_dir.name, node_key
                )

        return PiRunResult(
            status=status,
            exit_code=exit_code,
            command=command,
            run_dir=run_dir,
            session_dir=session_dir,
            error_message=error_message,
        )

    def _wait_for_process(
        self,
        proc: subprocess.Popen[Any],
        *,
        cancellation_token: CancellationToken | None,
        tracker: SubprocessTracker | None,
        execution_id: str,
    ) -> tuple[int, str]:
        start = time.monotonic()
        while True:
            if cancellation_token is not None and cancellation_token.wait(timeout=0.05):
                if tracker is not None:
                    tracker.cancel(execution_id)
                else:
                    self._terminate_direct(proc)
                return self._collect_exit(proc, cancelled=True)
            poll = proc.poll()
            if poll is not None:
                return poll, ""
            if time.monotonic() - start > self.config.timeout_seconds:
                if tracker is not None:
                    tracker.cancel(execution_id)
                else:
                    self._terminate_direct(proc)
                return -1, f"Pi session timed out after {self.config.timeout_seconds}s"

    def _terminate_direct(self, proc: subprocess.Popen[Any]) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=self.config.cancellation_grace_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _collect_exit(
        self, proc: subprocess.Popen[Any], cancelled: bool = False
    ) -> tuple[int, str]:
        try:
            exit_code = proc.wait(timeout=self.config.cancellation_grace_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            exit_code = -1
        error_message = "execution was cancelled" if cancelled else ""
        return exit_code, error_message

    def _detect_model_error(self, events_file: Path) -> str | None:
        """Scan Pi JSONL events for model-call failures reported by the CLI.

        Pi can exit with code 0 even when the upstream model request fails
        (e.g. a 400 from the provider). In that case the events file contains
        assistant messages whose ``stopReason`` is ``error`` and which carry an
        ``errorMessage``. Detecting this prevents us from reporting a misleading
        "Missing outputs" error when the agent never had a chance to run.
        """
        if not events_file.is_file():
            return None
        try:
            with events_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # message_start / message_end / turn_end wrap the assistant msg
                    msg = event.get("message") or {}
                    if not isinstance(msg, dict):
                        turn_end = event.get("turn_end") or {}
                        msg = turn_end.get("message") if isinstance(turn_end, dict) else {}
                    if isinstance(msg, dict) and msg.get("errorMessage"):
                        return str(msg["errorMessage"])

                    # message_update events nest under assistantMessageEvent
                    assistant_event = event.get("assistantMessageEvent") or {}
                    if isinstance(assistant_event, dict):
                        msg = assistant_event.get("message") or {}
                        if isinstance(msg, dict) and msg.get("errorMessage"):
                            return str(msg["errorMessage"])
        except Exception:
            return None
        return None

    def _build_prompt(
        self,
        *,
        job_id: str,
        node_key: str,
        job_dir: Path,
        skill_dir: Path,
        validator_script: Path,
        inputs: list[str],
        outputs: list[str],
    ) -> str:
        lines: list[str] = [
            "Execute the loaded node skill for this Video Hive workflow job.",
            "",
            f"Job ID: {job_id}",
            f"Node: {node_key}",
            f"Working directory: {job_dir}",
            f"Skill directory: {skill_dir}",
            f"Validator script: {validator_script}",
            "",
            "Declared inputs:",
        ]
        for inp in inputs:
            lines.append(f"- {inp}")
        lines.append("")
        lines.append("Required outputs:")
        for out in outputs:
            lines.append(f"- {out}")
        lines.append("")
        lines.append(
            "Write required outputs directly into the working directory. "
            "Do not modify inputs or create undeclared root-level artifacts. "
            "Finish after all required outputs are written and correct."
        )
        return "\n".join(lines) + "\n"
