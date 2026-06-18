from pathlib import Path


def resolve_workflow_skill(root: Path, relative_name: str) -> Path:
    """Resolve a workflow skill directory under root, validating it remains below root.

    Raises ValueError if the path escapes root or if required contract files are missing.
    """
    if not relative_name or relative_name.startswith("/") or ".." in relative_name.split("/"):
        raise ValueError(
            f"skill path must be a relative path without '..' components: {relative_name!r}"
        )

    skill_dir = (root / relative_name).resolve()
    root_resolved = root.resolve()
    try:
        skill_dir.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"skill path must remain below root: {relative_name!r}") from exc

    if not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"skill missing SKILL.md: {relative_name!r}")
    if not (skill_dir / "references" / "output-contract.md").is_file():
        raise ValueError(f"skill missing references/output-contract.md: {relative_name!r}")
    if not (skill_dir / "scripts" / "validate_output.py").is_file():
        raise ValueError(f"skill missing scripts/validate_output.py: {relative_name!r}")

    return skill_dir
