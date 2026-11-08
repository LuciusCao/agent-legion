"""Machine-contract checks shared by skill creation and editor validation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from server.app.services.skill_contract import contract_yaml_errors
from server.app.services.skill_contract_advisories import (
    contract_tier_warnings as contract_tier_warnings,
)
from server.app.services.skill_edit_checks import contract_errors

_CONTRACT_REQUIRED = (
    "SKILL.md",
    "references/output-contract.md",
    "scripts/validate_output.py",
    "contract.yaml",
)


def proposed_contract_errors(
    targets: list[tuple[Path, str]], repo_dir: Path
) -> list[dict[str, str]]:
    """Check the strict four-file birth contract against proposed content."""
    proposed = {
        path.relative_to(repo_dir.resolve()).as_posix(): content for path, content in targets
    }
    errors: list[dict[str, str]] = []
    skill_md = proposed.get("SKILL.md")
    if skill_md is None:
        errors.append({"path": "SKILL.md", "error": "missing SKILL.md"})
    elif not skill_md.strip():
        errors.append({"path": "SKILL.md", "error": "SKILL.md is empty"})
    for required in _CONTRACT_REQUIRED[1:]:
        if required not in proposed:
            errors.append({"path": required, "error": f"missing {required}"})
    contract_yaml = proposed.get("contract.yaml")
    if contract_yaml is not None:
        # Validate the proposed text, never unrelated on-disk state.
        with tempfile.TemporaryDirectory() as scratch:
            staged = Path(scratch)
            (staged / "contract.yaml").write_text(contract_yaml, encoding="utf-8")
            errors.extend(contract_yaml_errors(staged))
    return errors


def written_contract_errors(repo_dir: Path) -> list[dict[str, str]]:
    """Recheck the strict birth contract against the materialized repo."""
    errors = contract_errors(repo_dir)
    errors.extend(contract_yaml_errors(repo_dir))
    if not (repo_dir / "contract.yaml").is_file():
        errors.append({"path": "contract.yaml", "error": "missing contract.yaml"})
    return errors
