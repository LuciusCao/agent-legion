"""Shared git fixtures for the SkillManager test modules.

Split out of ``tests/test_skill_manager.py``: test modules must not import
each other (the pytest boundary check enforces it), so the bare-repo /
manager scaffolding both skill-manager test files use lives here. Names keep
their original underscore spelling so the importing test modules needed no
call-site churn.
"""

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


def _make_bare_repo(tmp_path: Path) -> str:
    env = _git_env()
    repo = tmp_path / "remote.git"
    repo.mkdir()
    # Pin the initial branch: fixtures reference `main`, but the default
    # branch of `git init` varies with the runner's git configuration (e.g.
    # CI defaults to `master`).
    subprocess.run(["git", "init", "--bare", "-b", "main", str(repo)], check=True, env=env)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True, env=env)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n")
    (clone / "references").mkdir()
    (clone / "references" / "output-contract.md").write_text("contract\n")
    (clone / "scripts").mkdir()
    (clone / "scripts" / "validate_output.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True, env=env)
    return f"file://{repo.resolve()}"


def _push_new_commit(repo_uri: str, tmp_path: Path, content: str) -> None:
    env = _git_env()
    work = tmp_path / "work" / "clone"
    (work / "SKILL.md").write_text(content)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "update", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True, env=env)


_KEY = "demo_workflow/generate_key_info"


def _make_manager(
    tmp_path: Path,
    skills: dict[str, dict[str, str]],
) -> SkillManager:
    return SkillManager(
        store=memory_skill_store(skills),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )


def _single_skill(repo_uri: str, ref: str = "main") -> dict[str, dict[str, str]]:
    return {_KEY: {"repo": repo_uri, "ref": ref}}
