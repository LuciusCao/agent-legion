"""Runs-dir location and leak-GC behavior for the skill runtime.

The runs dir is host-side scratch (seconds-scale snapshots + cache locks):
its default must be the OS temp dir, never a sibling of ``~/.agents/skills``
where leaked snapshots pollute the agent skills namespace, and stale
execution dirs must be sweepable so a hard-crash leak self-heals.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from server.app.skills.manager import SkillManager
from server.app.skills.paths import default_skills_runs_dir
from tests.helpers.skill_store import memory_skill_store

_KEY = "demo_workflow/generate_key_info"


def test_default_runs_dir_is_in_temp_dir() -> None:
    """The default lands under the OS temp dir with the deterministic
    prefixed name (same value on every call — the FileLock domain depends
    on all processes resolving this same path)."""
    import tempfile

    first = default_skills_runs_dir()
    assert first == default_skills_runs_dir()
    assert first.parent == Path(tempfile.gettempdir()).resolve() or first.parent == Path(
        tempfile.gettempdir()
    )
    assert first.name.startswith("agent-legion-skills.runs")


def test_default_runs_dir_has_uid_suffix_on_posix() -> None:
    if not hasattr(os, "getuid"):
        pytest.skip("posix-only assertion")
    assert default_skills_runs_dir().name.endswith(f"-{os.getuid()}")


def test_manager_default_runs_dir_uses_temp_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SkillManager built without runs_dir follows the temp default instead
    of sitting next to the skills base dir (the pollution bug)."""
    monkeypatch.setattr(
        "server.app.skills.manager.default_skills_runs_dir", lambda: tmp_path / "scratch"
    )
    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": "https://example.com/s.git", "ref": "main"}}),
        base_dir=tmp_path / "base" / "skills",
    )
    assert manager.runs_dir == tmp_path / "scratch"
    # The old default leaked scratch into the base dir's parent namespace.
    assert manager.runs_dir != manager.base_dir.parent / f"{manager.base_dir.name}.runs"


def test_sweep_removes_stale_execution_dirs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": "https://example.com/s.git", "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    runs = tmp_path / "runs"
    stale = runs / "stale-execution"
    (stale / "demo_workflow" / "generate_key_info").mkdir(parents=True)
    (stale / "demo_workflow" / "generate_key_info" / "SKILL.md").write_text("x", encoding="utf-8")

    fresh = runs / "fresh-execution"
    fresh.mkdir()

    locks = runs / ".locks"
    locks.mkdir()
    (locks / "demo_workflow--generate_key_info.lock").write_text("", encoding="utf-8")

    # Pin mtime in the distant past for the stale dir only.
    long_ago = time.time() - 7200
    os.utime(stale, (long_ago, long_ago))

    swept = manager.sweep_stale_executions(max_age_seconds=3600.0)

    assert swept == 1
    assert not stale.exists()
    assert fresh.exists()
    assert locks.exists()
    assert (locks / "demo_workflow--generate_key_info.lock").exists()


def test_sweep_missing_runs_dir_is_a_no_op(tmp_path: Path) -> None:
    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": "https://example.com/s.git", "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    assert manager.sweep_stale_executions() == 0


def test_sweep_never_follows_symlinked_execution_dirs(tmp_path: Path) -> None:
    """A symlinked execution dir is skipped, never rmtree'd through — the
    same escape protection _resolve_execution_dir enforces per id."""
    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": "https://example.com/s.git", "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep", encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "symlinked-execution").symlink_to(outside, target_is_directory=True)

    assert manager.sweep_stale_executions(max_age_seconds=0.0) == 0
    assert (outside / "sentinel.txt").exists()
    assert (runs / "symlinked-execution").is_symlink()


def test_sweep_ignores_stray_files(tmp_path: Path) -> None:
    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": "https://example.com/s.git", "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "stray.txt").write_text("x", encoding="utf-8")

    assert manager.sweep_stale_executions(max_age_seconds=0.0) == 0
    assert (runs / "stray.txt").exists()


def test_swept_execution_id_is_recreatable(tmp_path: Path) -> None:
    """A sweep racing a live dispatch is loud, not silent: after removal the
    run dir is simply rebuilt on the next get_skill_dir call."""
    import subprocess

    env = {**dict(os.environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    repo = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main", str(repo)], check=True, env=env)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True, env=env)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign", "--no-verify"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True, env=env)

    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": f"file://{repo.resolve()}", "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    execution_id = str(uuid.uuid4())
    first = manager.get_skill_dir(_KEY, execution_id)
    assert first.exists()
    assert manager.sweep_stale_executions(max_age_seconds=0.0) == 1
    second = manager.get_skill_dir(_KEY, execution_id)
    assert second.exists()


def test_ensure_secure_runs_dir_creates_private_root(tmp_path: Path) -> None:
    """First use creates the root with 0700, no parents=True: atomic, and a
    race with a pre-existing entry surfaces as an error instead of reuse."""
    import stat

    from server.app.skills.paths import ensure_secure_runs_dir

    root = tmp_path / "scratch"
    created = ensure_secure_runs_dir(root)
    assert created == root
    mode = stat.S_IMODE(root.stat().st_mode)
    assert mode == 0o700


def test_ensure_secure_runs_dir_reuses_validated_root(tmp_path: Path) -> None:
    from server.app.skills.paths import ensure_secure_runs_dir

    root = ensure_secure_runs_dir(tmp_path / "scratch")
    again = ensure_secure_runs_dir(root)
    assert again == root


def test_ensure_secure_runs_dir_rejects_symlink(tmp_path: Path) -> None:
    """A symlink at the predictable path must fail closed, never rmtree or
    copytree through it (shared /tmp pre-creation attack)."""
    from server.app.skills.paths import ensure_secure_runs_dir

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep", encoding="utf-8")
    target = tmp_path / "scratch"
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="refusing to use skills runs dir"):
        ensure_secure_runs_dir(target)
    assert (outside / "sentinel.txt").exists()


def test_ensure_secure_runs_dir_rejects_wrong_owner(tmp_path: Path) -> None:
    """A root owned by another uid (pre-created by an attacker on a shared
    temp dir) is refused, not reused."""
    from unittest.mock import patch

    from server.app.skills.paths import ensure_secure_runs_dir

    root = tmp_path / "scratch"
    root.mkdir(mode=0o700)
    real_lstat = os.lstat

    class _Foreign:
        st_mode = real_lstat(root).st_mode
        st_uid = real_lstat(root).st_uid + 1  # a different user

    with (
        patch("server.app.skills.paths.os.lstat", return_value=_Foreign()),
        pytest.raises(OSError, match="refusing to use skills runs dir"),
    ):
        ensure_secure_runs_dir(root)


def test_ensure_secure_runs_dir_tightens_wide_mode_when_owned(tmp_path: Path) -> None:
    """A 0777 root owned by the current user (older-version upgrade path or a
    permissive umask) is tightened to 0700 once, not refused."""
    import stat as stat_module

    from server.app.skills.paths import ensure_secure_runs_dir

    root = tmp_path / "scratch"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o777)

    ensured = ensure_secure_runs_dir(root)
    assert ensured == root
    assert stat_module.S_IMODE(root.stat().st_mode) == 0o700


def test_get_skill_dir_fails_closed_on_pre_created_runs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: dispatch into a symlinked runs dir fails loudly before
    any copytree, and the attack target stays untouched."""
    import subprocess

    env = {**dict(os.environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    repo = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main", str(repo)], check=True, env=env)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True, env=env)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign", "--no-verify"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True, env=env)

    outside = tmp_path / "attacker-drop"
    outside.mkdir()
    (outside / "evil").write_text("payload", encoding="utf-8")
    runs = tmp_path / "runs"
    runs.symlink_to(outside, target_is_directory=True)

    manager = SkillManager(
        store=memory_skill_store({_KEY: {"repo": f"file://{repo.resolve()}", "ref": "main"}}),
        base_dir=tmp_path / "skills",
        runs_dir=runs,
    )
    with pytest.raises(OSError, match="refusing to use skills runs dir"):
        manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert (outside / "evil").exists()
    assert not (outside / "demo_workflow").exists()
