from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from server.app.skills.manager import SkillManager


def noop_local_handler(
    _job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    return None


def write_output_handler(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def raising_local_handler(
    _job: dict[str, Any], _job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    raise ValueError("boom")


def logging_local_handler(
    _job: dict[str, Any], job_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    print("local handler log line")
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def record_runtime_handler(
    _job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None
) -> None:
    runtime = runtime or {}
    payload = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in runtime.items()
        if key
        in {
            "job_dir",
            "log_path",
            "inputs",
            "expected_outputs",
            "capability",
            "node_key",
            "workflow_key",
            "execution_id",
            "workspace_id",
        }
    }
    (job_dir / "runtime.json").write_text(json.dumps(payload), encoding="utf-8")
    (job_dir / "out.json").write_text("{}", encoding="utf-8")


def _make_skill_manager(
    tmp_path: Path,
    skill_key: str,
    validate_script: str | None = None,
) -> SkillManager:
    """Create a SkillManager backed by a temporary bare git repo for the given skill."""
    repo = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", str(repo)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (clone / "references").mkdir()
    (clone / "references" / "output-contract.md").write_text("contract\n", encoding="utf-8")
    (clone / "scripts").mkdir()
    if validate_script is not None:
        (clone / "scripts" / "validate_output.py").write_text(validate_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    env = {
        **dict(os.environ),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign", "--no-verify"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True)
    repo_uri = f"file://{repo.resolve()}"

    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  {skill_key}:\n    repo: {repo_uri}\n    ref: main\n",
        encoding="utf-8",
    )
    return SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
