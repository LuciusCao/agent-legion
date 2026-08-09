#!/usr/bin/env python3
"""Verify (and optionally sync) shared files across related Pi skills."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.skills.builtin_sources import BUILTIN_SKILL_SOURCES  # noqa: E402

SHARED_FILES = {
    "ability taxonomy": "references/question_comprehension_abilities.json",
}


def _resolve_local_repo(repo_url: str) -> Path | None:
    parsed = urlparse(repo_url)
    if parsed.scheme == "file":
        return Path(parsed.path).expanduser().resolve()
    if parsed.scheme:
        return None
    path = Path(repo_url).expanduser()
    return path.resolve() if path.is_absolute() else None


def _skills_for_workflow(config: dict, workflow: str) -> dict[str, str]:
    skills = config.get("skills", {})
    if not isinstance(skills, dict):
        raise ValueError("'skills' must be a mapping")
    return {
        key: source["repo"]
        for key, source in skills.items()
        if isinstance(source, dict) and key.startswith(f"{workflow}/") and "repo" in source
    }


def _check_and_sync(
    label: str,
    relative_path: str,
    local_skills: dict[str, Path],
    source_key: str,
    sync: bool,
) -> tuple[list[str], list[str]]:
    logger.info("Checking %s", label)
    errors: list[str] = []
    synced: list[str] = []
    contents: dict[str, bytes] = {}
    missing: list[str] = []

    for key, skill_dir in local_skills.items():
        file_path = skill_dir / relative_path
        if file_path.is_file():
            contents[key] = file_path.read_bytes()
        else:
            missing.append(key)

    if missing:
        if sync and source_key in missing:
            errors.append(f"{label}: source {source_key} is missing {relative_path}")
        elif not sync:
            errors.append(f"{label} ({relative_path}) missing in: {', '.join(missing)}")
        return errors, synced

    if sync:
        source_path = local_skills[source_key] / relative_path
        for key, content in contents.items():
            if key != source_key and content != contents[source_key]:
                target = local_skills[key] / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
                synced.append(key)
                logger.info("  synced %s -> %s", source_key, key)
    else:
        baseline_key = next(iter(contents))
        mismatches = [
            k for k, v in contents.items() if k != baseline_key and v != contents[baseline_key]
        ]
        if mismatches:
            errors.append(
                f"{label} ({relative_path}) differs. Baseline: {baseline_key}; "
                f"mismatched: {', '.join(mismatches)}"
            )

    if not errors and not synced:
        logger.info("  OK: %d skill(s) identical", len(contents))
    return errors, synced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check shared files across Pi skills.")
    parser.add_argument("--workflow", default="question_comprehension_info")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--source", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        skills = _skills_for_workflow(BUILTIN_SKILL_SOURCES.model_dump(), args.workflow)
    except Exception as exc:
        logger.error("invalid skills config: %s", exc)
        return 1

    if not skills:
        logger.error("no skills found for workflow %r", args.workflow)
        return 1

    local_skills: dict[str, Path] = {}
    remote_skills: list[str] = []
    for key, repo in skills.items():
        local_path = _resolve_local_repo(repo)
        if local_path:
            local_skills[key] = local_path
        else:
            remote_skills.append(key)

    if remote_skills:
        logger.warning("skipping remote skills: %s", ", ".join(remote_skills))
    if not local_skills:
        logger.error("no local skills to check for workflow %r", args.workflow)
        return 1

    source_key = args.source
    if source_key is not None and source_key not in local_skills:
        logger.error("source skill %r is not a local skill", source_key)
        return 1
    if args.sync:
        source_key = source_key or sorted(local_skills)[0]
        logger.info("Sync mode enabled; source: %s", source_key)

    all_errors: list[str] = []
    all_synced: list[str] = []
    for label, relative_path in SHARED_FILES.items():
        errors, synced = _check_and_sync(
            label, relative_path, local_skills, source_key or "", args.sync
        )
        all_errors.extend(errors)
        all_synced.extend(synced)

    if all_errors:
        logger.error("Shared file issues detected:")
        for error in all_errors:
            logger.error("  - %s", error)
        return 1

    if args.sync and all_synced:
        logger.info(
            "Synced shared files from %s to %d skill(s). "
            "Remember to commit, tag, and update the DB skill sources/lock "
            "(make skills-lock).",
            source_key,
            len(set(all_synced)),
        )
    else:
        logger.info("OK: shared files synchronized across %d skill(s)", len(local_skills))
    return 0


if __name__ == "__main__":
    sys.exit(main())
