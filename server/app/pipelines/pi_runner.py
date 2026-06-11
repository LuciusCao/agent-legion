from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PiConfig:
    binary: str = "pi"
    provider: str = ""
    model: str = ""
    thinking: str = "low"
    timeout_seconds: int = 600
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PiRunResult:
    status: str
    exit_code: int
    command: list[str]
    run_dir: Path
    session_dir: Path
    error_message: str = ""


class PiRunner:
    def __init__(self, config: PiConfig, skill_root: Path):
        self.config = config
        self.skill_root = skill_root

    @classmethod
    def from_config(cls, raw: dict[str, Any], skill_root: Path) -> PiRunner:
        binary = raw.get("binary")
        if not binary or not isinstance(binary, str):
            raise ValueError("Pi binary is required")
        timeout = raw.get("timeout_seconds", 600)
        if not isinstance(timeout, int) or timeout < 1:
            raise ValueError("Pi timeout_seconds must be a positive integer")
        env = raw.get("environment", {})
        if not isinstance(env, dict):
            env = {}
        config = PiConfig(
            binary=binary,
            provider=str(raw.get("provider", "")),
            model=str(raw.get("model", "")),
            thinking=str(raw.get("thinking", "low")),
            timeout_seconds=timeout,
            environment={str(k): str(v) for k, v in env.items()},
        )
        return cls(config, skill_root)

    def build_command(
        self,
        *,
        skill_dir: Path,
        session_dir: Path,
        tools: list[str],
        session_name: str,
        prompt_file: Path,
    ) -> list[str]:
        cmd: list[str] = [
            self.config.binary,
            "--mode",
            "json",
            "--session-dir",
            str(session_dir),
            "--name",
            session_name,
            "--no-context-files",
            "--no-extensions",
            "--no-prompt-templates",
            "--no-skills",
            "--skill",
            str(skill_dir),
            "--tools",
            ",".join(tools),
            "--approve",
        ]
        if self.config.provider:
            cmd.extend(["--provider", self.config.provider])
        if self.config.model:
            cmd.extend(["--model", self.config.model])
        if self.config.thinking:
            cmd.extend(["--thinking", self.config.thinking])
        cmd.extend([str(prompt_file), "Execute the attached node instructions."])
        return cmd

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
    ) -> PiRunResult:
        job_dir = Path(str(job["storage_dir"]))
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
        command = self.build_command(
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
            if job_db is not None:
                run_record = job_db.start_node_run(
                    job["id"],
                    node_key,
                    command,
                    str(events_file),
                    run_dir=str(run_dir),
                    session_dir=str(session_dir),
                )
                if run_record is None:
                    return PiRunResult(
                        status="skipped",
                        exit_code=0,
                        command=command,
                        run_dir=run_dir,
                        session_dir=session_dir,
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
                )
                try:
                    exit_code = proc.wait(timeout=self.config.timeout_seconds)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    exit_code = -1
                    error_message = f"Pi session timed out after {self.config.timeout_seconds}s"
        except FileNotFoundError:
            exit_code = 127
            error_message = f"Pi binary not found: {self.config.binary}"
        except Exception as exc:
            exit_code = 1
            error_message = str(exc)

        # Validate outputs
        if exit_code == 0:
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
            "error_message": error_message,
        }
        (run_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2))

        status = "completed" if exit_code == 0 else "failed"
        if job_db is not None and run_record is not None:
            job_db.finish_node_run(run_record["id"], status, exit_code, error_message)

        return PiRunResult(
            status=status,
            exit_code=exit_code,
            command=command,
            run_dir=run_dir,
            session_dir=session_dir,
            error_message=error_message,
        )

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
            "Execute the loaded node skill for this Video Hive pipeline job.",
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
