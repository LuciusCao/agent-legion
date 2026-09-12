"""Shared path/contract checks for skill repo mutation services (#633).

``SkillEditingService.save_version`` (services/skill_editing.py) and
``SkillCreationService.create_skill`` (services/skill_creation.py) validate
skill file paths and the runtime contract trio identically; the helpers
live here once so the two flows cannot drift (extracted from
skill_editing.py, no behaviour change).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def contract_errors(content_dir: Path) -> list[dict[str, str]]:
    """The runtime skill contract (``workflows/skills.py`` enforces it at
    dispatch): non-empty SKILL.md + references/output-contract.md +
    scripts/validate_output.py, reported as a structured error list."""
    if not content_dir.is_dir():
        return [{"path": ".", "error": "skill directory does not exist"}]
    errors: list[dict[str, str]] = []
    skill_md = content_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append({"path": "SKILL.md", "error": "missing SKILL.md"})
    elif not skill_md.read_text(encoding="utf-8", errors="replace").strip():
        errors.append({"path": "SKILL.md", "error": "SKILL.md is empty"})
    for required in ("references/output-contract.md", "scripts/validate_output.py"):
        if not (content_dir / required).is_file():
            errors.append({"path": required, "error": f"missing {required}"})
    return errors


def target_path_errors(raw_paths: list[str]) -> list[dict[str, str]]:
    """Path-safety errors for the given relative paths (empty list = ok):
    non-empty, relative, no ``..``, no ``.git`` at any level or case (on
    case-insensitive filesystems ``.GIT/hooks/`` still lands inside the git
    metadata dir)."""
    errors: list[dict[str, str]] = []
    for raw in raw_paths:
        parts = PurePosixPath(raw).parts
        if (
            not raw
            or PurePosixPath(raw).is_absolute()
            or ".." in parts
            or any(part.lower() == ".git" for part in parts)
        ):
            errors.append(
                {
                    "path": raw or ".",
                    "error": "path must be relative, stay inside the skill directory, "
                    "and not touch .git",
                }
            )
    return errors


def resolve_targets_checked(
    root_dir: Path, files: list[tuple[str, str]]
) -> tuple[list[tuple[Path, str]], list[dict[str, str]]]:
    """Resolve (targets, errors): every path either resolves inside root_dir
    (staying under it after symlink resolution) or lands in errors."""
    errors = target_path_errors([raw for raw, _ in files])
    rejected = {error["path"] for error in errors}
    targets: list[tuple[Path, str]] = []
    root = root_dir.resolve()
    for raw, content in files:
        if (raw or ".") in rejected:
            continue
        resolved = (root / raw).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            errors.append({"path": raw, "error": "path escapes the skill directory"})
            continue
        targets.append((resolved, content))
    return targets, errors
