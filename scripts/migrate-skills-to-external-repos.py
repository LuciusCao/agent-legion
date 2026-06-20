#!/usr/bin/env python3
"""Migrate repository-owned Pi skills into standalone external git repositories.

Each capability directory under ``server/app/workflows/skills/<workflow>/`` becomes
an independent git repository at ``~/.agents/skills/agent-legion/<workflow>/<capability>/``.
Shared helper modules from ``<workflow>/_shared/`` are copied into each capability repo
so the resulting repo is self-contained.

The script is idempotent: if a target repository already exists, it is removed and
recreated from the current source tree.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE_ROOT = Path("server/app/workflows/skills")
TARGET_ROOT = Path.home() / ".agents" / "skills" / "agent-legion"

OLD_SHARED_PARENT = "Path(__file__).resolve().parents[2]"
NEW_SHARED_PARENT = "Path(__file__).resolve().parents[1]"


def discover_capabilities(source_root: Path) -> list[tuple[Path, Path, str]]:
    """Return (workflow_dir, capability_dir, capability_name) tuples."""
    capabilities: list[tuple[Path, Path, str]] = []
    if not source_root.is_dir():
        return capabilities

    for workflow_dir in sorted(source_root.iterdir()):
        if not workflow_dir.is_dir():
            continue
        for capability_dir in sorted(workflow_dir.iterdir()):
            if not capability_dir.is_dir() or capability_dir.name == "_shared":
                continue
            if capability_dir.name == "__pycache__":
                continue
            capabilities.append((workflow_dir, capability_dir, capability_dir.name))
    return capabilities


def copy_tree(src: Path, dst: Path) -> None:
    """Copy a directory tree, ignoring VCS metadata and Python caches."""
    ignore = shutil.ignore_patterns(
        ".git",
        "__pycache__",
        "*.pyc",
        "*.pyo",
    )
    shutil.copytree(src, dst, ignore=ignore)


def rewrite_shared_path(target_capability: Path) -> tuple[int, list[Path]]:
    """Update Python references to the relocated _shared directory.

    Replaces ``parents[2]`` with ``parents[1]`` in any
    ``Path(__file__).resolve().parents[2]`` expression so that the copied
    ``_shared`` package at the repo root is found.

    Returns a tuple of (modified_count, skipped_paths). A path is recorded as
    skipped when it contains ``parents[2]`` but does not match the expected
    replacement pattern.
    """
    modified = 0
    skipped: list[Path] = []
    for script_path in target_capability.rglob("*.py"):
        original = script_path.read_text(encoding="utf-8")
        if OLD_SHARED_PARENT in original:
            updated = original.replace(OLD_SHARED_PARENT, NEW_SHARED_PARENT)
            if updated != original:
                script_path.write_text(updated, encoding="utf-8")
                modified += 1
                logger.info("Rewrote shared path in %s", script_path)
        elif "parents[2]" in original:
            skipped.append(script_path)
            logger.warning(
                "Skipped shared path rewrite in %s (contains parents[2] but not expected pattern)",
                script_path,
            )
    return modified, skipped


def init_git_repo(target_dir: Path) -> None:
    """Initialize a git repo and commit all contents."""
    subprocess.run(["git", "init", "-q"], cwd=target_dir, check=True)
    subprocess.run(
        ["git", "config", "user.email", "migration@video-hive.local"], cwd=target_dir, check=True
    )
    subprocess.run(["git", "config", "user.name", "Skill Migration"], cwd=target_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=target_dir, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "chore: initialize skill repo from Video Hive"],
        cwd=target_dir,
        check=True,
    )


def push_repo(target_dir: Path, remote_url: str) -> None:
    """Add remote and push the initial commit."""
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=target_dir, check=True)
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=target_dir, check=True)
    logger.info("Pushed %s to %s", target_dir.name, remote_url)


def migrate_capability(
    workflow_dir: Path,
    capability_dir: Path,
    capability_name: str,
    target_root: Path,
    push: bool,
    remote_template: str | None,
) -> Path:
    """Migrate a single capability to an external git repo."""
    workflow_name = workflow_dir.name
    target_dir = target_root / workflow_name / capability_name

    if target_dir.exists():
        logger.info("Removing existing target %s", target_dir)
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)

    copy_tree(capability_dir, target_dir)

    shared_dir = workflow_dir / "_shared"
    if shared_dir.is_dir():
        copy_tree(shared_dir, target_dir / "_shared")

    modified, skipped = rewrite_shared_path(target_dir)
    if modified:
        logger.info("Rewrote shared path in %d file(s)", modified)
    if skipped:
        logger.warning("Skipped shared path rewrite in %d file(s)", len(skipped))

    init_git_repo(target_dir)

    if push and remote_template:
        remote_url = remote_template.format(
            workflow=workflow_name,
            capability=capability_name,
        )
        push_repo(target_dir, remote_url)

    logger.info("Migrated %s/%s -> %s", workflow_name, capability_name, target_dir)
    return target_dir


def verify_migration(targets: list[tuple[Path, Path]]) -> bool:
    """Verify each target is a git repo with the expected files.

    Each item in ``targets`` is a ``(target_dir, workflow_dir)`` tuple. The
    presence of ``_shared`` in the target is only enforced when the source
    workflow directory also contains ``_shared``.
    """
    ok = True
    for target, workflow_dir in targets:
        git_dir = target / ".git"
        skill_md = target / "SKILL.md"
        shared_dir = target / "_shared"
        validate_script = target / "scripts" / "validate_output.py"
        source_shared_dir = workflow_dir / "_shared"
        expects_shared = source_shared_dir.is_dir()

        if not git_dir.is_dir():
            logger.error("Missing .git in %s", target)
            ok = False
        if not skill_md.is_file():
            logger.error("Missing SKILL.md in %s", target)
            ok = False
        if expects_shared and not shared_dir.is_dir():
            logger.error("Missing _shared in %s (expected because source has _shared)", target)
            ok = False
        if not validate_script.is_file():
            logger.error("Missing scripts/validate_output.py in %s", target)
            ok = False
        else:
            text = validate_script.read_text(encoding="utf-8")
            if OLD_SHARED_PARENT in text:
                logger.error("Stale _shared reference in %s", validate_script)
                ok = False
            if NEW_SHARED_PARENT not in text:
                logger.warning("Expected new _shared reference not found in %s", validate_script)

        if git_dir.is_dir():
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=target,
                capture_output=True,
                text=True,
                check=True,
            )
            commit_count = int(result.stdout.strip())
            if commit_count < 1:
                logger.error("No commits in %s", target)
                ok = False
            else:
                logger.info("%s has %d commit(s)", target.name, commit_count)

    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate repository-owned Pi skills to external git repositories.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=SOURCE_ROOT,
        help="Root directory containing workflow skill directories",
    )
    parser.add_argument(
        "--target-root",
        type=Path,
        default=TARGET_ROOT,
        help="Root directory where external skill repositories will be created",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push each created repository to its configured remote",
    )
    parser.add_argument(
        "--remote-template",
        default=None,
        help="Remote URL template with {workflow} and {capability} placeholders",
    )
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="Remove source skill directories after successful migration and verification",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated without modifying anything",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    capabilities = discover_capabilities(args.source_root)
    if not capabilities:
        logger.warning("No capabilities found under %s", args.source_root)
        return 0

    logger.info("Discovered %d capability(ies)", len(capabilities))
    for workflow_dir, _capability_dir, capability_name in capabilities:
        logger.info("  %s/%s", workflow_dir.name, capability_name)

    if args.dry_run:
        return 0

    if args.push and not args.remote_template:
        parser.error("--push requires --remote-template")

    try:
        targets: list[tuple[Path, Path]] = []
        for workflow_dir, capability_dir, capability_name in capabilities:
            target = migrate_capability(
                workflow_dir,
                capability_dir,
                capability_name,
                args.target_root,
                args.push,
                args.remote_template,
            )
            targets.append((target, workflow_dir))

        logger.info("Verifying %d migrated repo(s)...", len(targets))
        if not verify_migration(targets):
            logger.error("Migration verification failed; source directories left intact.")
            return 1

        logger.info("Migration verified successfully.")

        if args.delete_source:
            for _workflow_dir, capability_dir, _capability_name in capabilities:
                logger.info("Removing source %s", capability_dir)
                shutil.rmtree(capability_dir)
            logger.info("Source directories removed.")

        return 0
    except subprocess.CalledProcessError as exc:
        logger.error("Command failed: %s", exc)
        if exc.stderr:
            logger.error("%s", exc.stderr)
        return 1
    except (OSError, shutil.Error) as exc:
        logger.error("Filesystem error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
