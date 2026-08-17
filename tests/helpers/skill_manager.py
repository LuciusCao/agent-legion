from __future__ import annotations

import os
import subprocess
from pathlib import Path

from server.app.skills.manager import SkillManager
from tests.helpers.skill_store import memory_skill_store


def _git_env() -> dict[str, str]:
    env = {**dict(os.environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    # When invoked from a git hook (e.g. pre-commit), these variables point at
    # the parent repository. They must not leak into the temporary test repos,
    # otherwise commands like `git -C <tmp> push origin HEAD` would operate on
    # the parent repo and push to its remote instead of the local bare repo.
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


def _make_skill_manager(
    tmp_path: Path,
    skill_key: str,
    validate_script: str | None = None,
) -> SkillManager:
    """Create a SkillManager backed by a temporary bare git repo for the given skill."""
    env = _git_env()
    repo = tmp_path / "remote.git"
    repo.mkdir()
    # Pin the initial branch: the skills.yaml below references `main`, but the
    # default branch of `git init` varies with the runner's git configuration
    # (e.g. CI defaults to `master`).
    subprocess.run(["git", "init", "--bare", "-b", "main", str(repo)], check=True, env=env)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True, env=env)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (clone / "references").mkdir()
    (clone / "references" / "output-contract.md").write_text("contract\n", encoding="utf-8")
    (clone / "scripts").mkdir()
    if validate_script is not None:
        (clone / "scripts" / "validate_output.py").write_text(validate_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign", "--no-verify"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True, env=env)
    repo_uri = f"file://{repo.resolve()}"

    return SkillManager(
        store=memory_skill_store({skill_key: {"repo": repo_uri, "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
