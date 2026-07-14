from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ZERO_SHA = "0" * 40


def _run(
    args: list[str | Path],
    *,
    cwd: Path,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    for name in tuple(process_env):
        if name.startswith("GIT_"):
            process_env.pop(name)
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        input=input_text,
        env=process_env,
        text=True,
        capture_output=True,
        check=check,
    )


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def hook_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".githooks").mkdir()
    (repo / "scripts").mkdir()
    shutil.copy2(PROJECT_ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run-local-gate.sh",
        repo / "scripts" / "run-local-gate.sh",
    )
    gate_log = tmp_path / "gate.log"
    for gate, script_name in (("quick", "check-quick.sh"), ("full", "check.sh")):
        _write_executable(
            repo / "scripts" / script_name,
            "#!/usr/bin/env bash\n"
            'if [[ -n "${GIT_DIR:-}${GIT_WORK_TREE:-}" ]]; then exit 99; fi\n'
            f"printf '{gate}\\n' >>\"$GATE_LOG\"\n",
        )

    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "local-gate@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Local Gate Test"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-qm", "fixture"], cwd=repo)
    return repo, gate_log


def _push_input(repo: Path, remote_ref: str) -> str:
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    return f"refs/heads/local {head} {remote_ref} {ZERO_SHA}\n"


def _hook_env(gate_log: Path) -> dict[str, str]:
    return {"GATE_LOG": str(gate_log)}


def test_git_commands_ignore_inherited_repository_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inherited_repo = tmp_path / "inherited"
    isolated_repo = tmp_path / "isolated"
    inherited_repo.mkdir()
    isolated_repo.mkdir()
    _run(["git", "init", "-q"], cwd=inherited_repo)
    monkeypatch.setenv("GIT_DIR", str(inherited_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(inherited_repo))

    _run(["git", "init", "-q"], cwd=isolated_repo)

    assert (isolated_repo / ".git").is_dir()


def test_feature_push_runs_quick_gate_once_and_reuses_evidence(
    hook_repo: tuple[Path, Path],
) -> None:
    repo, gate_log = hook_repo
    push_input = _push_input(repo, "refs/heads/feature/test")

    first = _run(
        [repo / ".githooks" / "pre-push"],
        cwd=repo,
        input_text=push_input,
        env={
            **_hook_env(gate_log),
            "GIT_DIR": str(repo / ".git"),
            "GIT_WORK_TREE": str(repo),
        },
    )
    second = _run(
        [repo / ".githooks" / "pre-push"],
        cwd=repo,
        input_text=push_input,
        env=_hook_env(gate_log),
    )

    assert gate_log.read_text(encoding="utf-8").splitlines() == ["quick"]
    assert "Running local quick gate" in first.stdout
    assert "reusing cached evidence" in second.stdout


@pytest.mark.parametrize("remote_ref", ["refs/heads/develop", "refs/tags/v1.0.0"])
def test_protected_ref_push_runs_full_gate(hook_repo: tuple[Path, Path], remote_ref: str) -> None:
    repo, gate_log = hook_repo

    result = _run(
        [repo / ".githooks" / "pre-push"],
        cwd=repo,
        input_text=_push_input(repo, remote_ref),
        env=_hook_env(gate_log),
    )

    assert gate_log.read_text(encoding="utf-8").splitlines() == ["full"]
    assert "Running local full gate" in result.stdout


def test_pre_push_rejects_dirty_worktree(hook_repo: tuple[Path, Path]) -> None:
    repo, gate_log = hook_repo
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    result = _run(
        [repo / ".githooks" / "pre-push"],
        cwd=repo,
        input_text=_push_input(repo, "refs/heads/feature/test"),
        env=_hook_env(gate_log),
        check=False,
    )

    assert result.returncode == 1
    assert "worktree is not clean" in result.stderr
    assert not gate_log.exists()


def test_install_script_configures_versioned_hooks_in_linked_worktree(tmp_path: Path) -> None:
    main_repo = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main_repo.mkdir()
    _run(["git", "init", "-q"], cwd=main_repo)
    _run(["git", "config", "user.email", "local-gate@example.com"], cwd=main_repo)
    _run(["git", "config", "user.name", "Local Gate Test"], cwd=main_repo)
    (main_repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    _run(["git", "add", "."], cwd=main_repo)
    _run(["git", "commit", "-qm", "fixture"], cwd=main_repo)
    _run(["git", "worktree", "add", "-q", "-b", "test-worktree", worktree], cwd=main_repo)

    (worktree / ".githooks").mkdir()
    (worktree / "scripts" / "git-hooks").mkdir(parents=True)
    shutil.copy2(
        PROJECT_ROOT / ".githooks" / "pre-commit",
        worktree / ".githooks" / "pre-commit",
    )
    shutil.copy2(
        PROJECT_ROOT / ".githooks" / "pre-push",
        worktree / ".githooks" / "pre-push",
    )
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run-local-gate.sh",
        worktree / "scripts" / "run-local-gate.sh",
    )
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "install-git-hooks.sh",
        worktree / "scripts" / "install-git-hooks.sh",
    )
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "git-hooks" / "pre-commit",
        worktree / "scripts" / "git-hooks" / "pre-commit",
    )
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "git-hooks" / "pre-push",
        worktree / "scripts" / "git-hooks" / "pre-push",
    )

    _run([worktree / "scripts" / "install-git-hooks.sh"], cwd=worktree)

    common_dir = Path(_run(["git", "rev-parse", "--git-common-dir"], cwd=worktree).stdout.strip())
    if not common_dir.is_absolute():
        common_dir = worktree / common_dir
    assert (common_dir / "hooks" / "pre-commit").is_file()
    assert (common_dir / "hooks" / "pre-push").is_file()
    configured = _run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=worktree,
        check=False,
    )
    assert configured.returncode == 1
