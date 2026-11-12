"""Exercise the real Host skill checkout path without external services."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SKILL_KEY = "host_smoke/skill"
PINNED_REF = "v1.0.0"
CONTRACT_FILES = (
    "SKILL.md",
    "references/output-contract.md",
    "scripts/validate_output.py",
)


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="host-smoke",
        GIT_AUTHOR_EMAIL="host-smoke@example.invalid",
        GIT_COMMITTER_NAME="host-smoke",
        GIT_COMMITTER_EMAIL="host-smoke@example.invalid",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _write_contract(repo: Path, version: str) -> None:
    (repo / "references").mkdir(exist_ok=True)
    (repo / "scripts").mkdir(exist_ok=True)
    contents = {
        "SKILL.md": f"# Host checkout smoke {version}\n",
        "references/output-contract.md": f"contract {version}\n",
        "scripts/validate_output.py": f"print('ok')  # {version}\n",
    }
    for relative_path, content in contents.items():
        (repo / relative_path).write_text(content, encoding="utf-8")


def prepare_workspace(workspace: Path) -> dict[str, str]:
    """Create distinct pinned and latest commits in a minimal in-place skill repo."""
    repo = workspace / "skills" / SKILL_KEY
    if repo.exists():
        raise ValueError(f"smoke skill repo already exists: {repo}")
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write_contract(repo, "pinned")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "pinned", "--no-gpg-sign")
    pinned_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", PINNED_REF)

    _write_contract(repo, "latest")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "latest", "--no-gpg-sign")
    latest_commit = _git(repo, "rev-parse", "HEAD")
    commits = {"pinned_commit": pinned_commit, "latest_commit": latest_commit}
    (workspace / "commits.json").write_text(json.dumps(commits), encoding="utf-8")
    return commits


def _assert_checkout(checkout: Any, *, ref: str, commit: str, content: str) -> None:
    assert checkout.key == SKILL_KEY
    assert checkout.ref == ref
    assert checkout.commit == commit
    assert checkout.version == f"{ref}@{commit[:12]}"
    assert checkout.manifest_pins() == {
        "skill": SKILL_KEY,
        "skill_ref": ref,
        "skill_version": f"{ref}@{commit[:12]}",
        "skill_commit": commit,
    }
    expected_contents = {
        "SKILL.md": f"# Host checkout smoke {content}\n",
        "references/output-contract.md": f"contract {content}\n",
        "scripts/validate_output.py": f"print('ok')  # {content}\n",
    }
    for relative_path in CONTRACT_FILES:
        assert (checkout.run_dir / relative_path).read_text(encoding="utf-8") == expected_contents[
            relative_path
        ]


def run_smoke(workspace: Path) -> dict[str, str]:
    """Resolve latest, first-pin, and relocked checkouts through production code."""
    from server.app.skills.checkout import resolve_skill_checkout
    from server.app.skills.config import SkillsLock
    from server.app.skills.manager import SkillManager

    class MemorySkillStore:
        def __init__(self) -> None:
            self.lock: SkillsLock | None = None

        def get_lock(self) -> SkillsLock | None:
            return self.lock.model_copy(deep=True) if self.lock is not None else None

        def put_lock(self, lock: SkillsLock) -> None:
            self.lock = lock.model_copy(deep=True)

    repo = workspace / "skills" / SKILL_KEY
    commits = json.loads((workspace / "commits.json").read_text(encoding="utf-8"))
    store = MemorySkillStore()
    base_dir = workspace / "skills"
    runs_dir = workspace / "runs"
    manager = SkillManager(store=store, base_dir=base_dir, runs_dir=runs_dir)

    latest = resolve_skill_checkout(manager, SKILL_KEY, "latest")
    _assert_checkout(latest, ref="latest", commit=commits["latest_commit"], content="latest")
    assert manager.load_lock().skills == {}

    pinned = resolve_skill_checkout(manager, SKILL_KEY, "pinned-first", PINNED_REF)
    _assert_checkout(pinned, ref=PINNED_REF, commit=commits["pinned_commit"], content="pinned")
    assert store.lock is not None
    assert store.lock.skills[SKILL_KEY].refs[PINNED_REF] == commits["pinned_commit"]

    _git(repo, "tag", "-f", PINNED_REF, commits["latest_commit"])
    relocked_manager = SkillManager(store=store, base_dir=base_dir, runs_dir=runs_dir)
    relocked = resolve_skill_checkout(relocked_manager, SKILL_KEY, "pinned-relocked", PINNED_REF)
    _assert_checkout(relocked, ref=PINNED_REF, commit=commits["pinned_commit"], content="pinned")
    return commits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("all", "prepare", "run"), nargs="?", default="all")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.phase == "all":
        if args.workspace is not None:
            parser.error("--workspace is only valid with prepare or run")
        with tempfile.TemporaryDirectory(prefix="host-skill-checkout-") as directory:
            workspace = Path(directory)
            prepare_workspace(workspace)
            result = run_smoke(workspace)
    elif args.phase == "prepare":
        if args.workspace is None:
            parser.error("prepare requires --workspace")
        result = prepare_workspace(args.workspace)
    else:
        if args.workspace is None:
            parser.error("run requires --workspace")
        result = run_smoke(args.workspace)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
