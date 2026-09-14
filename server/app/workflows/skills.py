from pathlib import Path

# The dispatch/validation contract trio every materialized skill tree must
# carry. Single source of truth: resolve_workflow_skill rejects a tree
# missing any of these, and the shared commit cache probe
# (skills/commit_cache.py) treats their absence as a corrupted hit (#638).
REQUIRED_CONTRACT_FILES: tuple[str, ...] = (
    "SKILL.md",
    "references/output-contract.md",
    "scripts/validate_output.py",
)


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

    for contract_file in REQUIRED_CONTRACT_FILES:
        if not (skill_dir / contract_file).is_file():
            raise ValueError(f"skill missing {contract_file}: {relative_name!r}")

    return skill_dir
