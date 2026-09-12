"""Unit tests for SkillCreationService (#633).

The route-level behavior lives in
tests/routes/test_studio_agent_skill_creation_tools.py; here the focus is
the all-or-nothing guarantee that no half-initialized repo survives a failed
create, plus the cleanup identity re-verification.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from server.app.services import skill_creation
from server.app.services.job_errors import NotFoundError
from server.app.services.skill_creation import SkillCreationService
from server.app.services.skill_editing import SkillFileWrite
from server.app.services.skill_repo import SkillGitError

_TRIO = [
    SkillFileWrite(path="SKILL.md", content="# Skill\n"),
    SkillFileWrite(path="references/output-contract.md", content="# contract\n"),
    SkillFileWrite(path="scripts/validate_output.py", content="raise SystemExit(0)\n"),
]


class _FakeJobDB:
    """Only get_workspace is needed for the create flow."""

    def get_workspace(self, workspace_id: str):
        return {"id": workspace_id} if workspace_id == "ws-1" else None


def _service(runs_dir: Path) -> SkillCreationService:
    return SkillCreationService(_FakeJobDB(), runs_dir=runs_dir)


def _git_subcommand(args: list[str]) -> str:
    """First token that is neither a flag nor a -c flag's value."""
    skip_value = False
    for token in args:
        if skip_value:
            skip_value = False
            continue
        if token in ("-c", "-C", "-m", "--git-dir", "--work-tree"):
            skip_value = True
            continue
        if token.startswith("-"):
            continue
        return token
    return args[0]


@pytest.fixture
def home(tmp_path, monkeypatch):
    base = tmp_path / "home" / ".agents" / "skills"
    base.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return base


def test_failed_git_step_removes_partial_repo(home, tmp_path) -> None:
    service = _service(tmp_path / "runs")
    calls: list[str] = []

    real_git = SkillCreationService.__dict__["_git"].__func__
    monkey = pytest.MonkeyPatch()

    def failing_git(repo_dir: Path, args: list[str], *, check: bool = True):
        # The commit call leads with -c identity flags; the first non-flag,
        # non-flag-value token is the subcommand ("commit" here — the -c
        # values contain '=' or match git config keys).
        subcommand = _git_subcommand(args)
        calls.append(subcommand)
        if subcommand == "commit":
            # Simulate a commit failure after files were written.
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")
        return real_git(repo_dir, args, check=check)

    monkey.setattr(SkillCreationService, "_git", staticmethod(failing_git))
    try:
        with pytest.raises(SkillGitError):
            service.create_skill("ws-1", "broken", list(_TRIO), "v0.1.0", "m")
    finally:
        monkey.undo()

    # init ran, then the commit failed — and the partial dir is gone.
    assert "init" in calls and "commit" in calls
    repo = home / "ws-1" / "broken"
    assert not repo.exists()

    # A retry (with git healthy again) succeeds — no wedged state.
    result = service.create_skill("ws-1", "broken", list(_TRIO), "v0.1.0", "m")
    assert result["key"] == "ws-1/broken"
    assert repo.is_dir()


def test_cleanup_refuses_to_delete_a_swapped_directory(home, tmp_path) -> None:
    # The identity check: a directory replaced between mkdir and cleanup must
    # survive (never rm something we did not just create).
    repo = home / "ws-1" / "swapped"
    repo.mkdir(parents=True)
    (repo / "precious.txt").write_text("do not delete", encoding="utf-8")
    identity = (0, 0)  # an identity that will never match
    skill_creation._remove_created_repo(repo, identity)
    assert (repo / "precious.txt").read_text(encoding="utf-8") == "do not delete"

    # The true identity DOES remove the freshly created directory.
    st = os.lstat(repo)
    assert stat.S_ISDIR(st.st_mode)
    skill_creation._remove_created_repo(repo, (st.st_dev, st.st_ino))
    assert not repo.exists()


def test_create_skill_unknown_workspace_is_404(home, tmp_path) -> None:
    with pytest.raises(NotFoundError):
        _service(tmp_path / "runs").create_skill("ws-x", "a", list(_TRIO), "v1", "m")
