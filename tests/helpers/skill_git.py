"""Shared git fixtures for the SkillManager test modules.

In-place is the only mode since #322: a skill's repo IS the directory
``<base_dir>/<group>/<name>`` — there is no remote/clone channel anymore.
These helpers create such repos (with the dispatch contract trio) and
managers pointing at them. Names keep their original underscore spelling so
the importing test modules needed no call-site churn.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from server.app.skills.manager import SkillManager
from tests.helpers.skill_store import memory_skill_store

_KEY = "demo_workflow/generate_key_info"


def _git_env() -> dict[str, str]:
    env = {**dict(os.environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    # When invoked from a git hook (e.g. pre-commit), these variables point at
    # the parent repository. They must not leak into the temporary test repos.
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    ).stdout.strip()


def _make_skill_repo(
    base_dir: Path,
    key: str = _KEY,
    *,
    content: str = "# skill\n",
    validate_script: str | None = "print('ok')\n",
) -> Path:
    """Create an in-place git repo at ``<base_dir>/<key>`` with the contract trio."""
    repo = base_dir / key
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "SKILL.md").write_text(content)
    (repo / "references").mkdir()
    (repo / "references" / "output-contract.md").write_text("contract\n")
    (repo / "scripts").mkdir()
    if validate_script is not None:
        (repo / "scripts" / "validate_output.py").write_text(validate_script)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-gpg-sign")
    return repo


def _commit_skill_update(repo: Path, content: str) -> str:
    """Rewrite SKILL.md and commit; return the new HEAD commit."""
    (repo / "SKILL.md").write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "update", "--no-gpg-sign")
    return _head_commit(repo)


def _head_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _tag(repo: Path, tag: str, *, force: bool = False) -> str:
    """Tag HEAD (``force`` moves an existing tag); return the tagged commit."""
    _git(repo, "tag", *(["-f"] if force else []), tag)
    return _git(repo, "rev-parse", f"{tag}^{{commit}}")


def _make_manager(tmp_path: Path, lock: dict | None = None) -> SkillManager:
    return SkillManager(
        store=memory_skill_store(lock=lock),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
