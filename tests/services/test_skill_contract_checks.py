"""Focused contract checks extracted from skill creation and validation."""

from pathlib import Path

import pytest

from server.app.services.skill_contract_checks import (
    contract_tier_warnings,
    proposed_contract_errors,
    written_contract_errors,
)

pytestmark = pytest.mark.no_db


def _targets(repo: Path) -> list[tuple[Path, str]]:
    return [
        (repo / "SKILL.md", "# Skill\n"),
        (repo / "references/output-contract.md", "# contract\n"),
        (repo / "scripts/validate_output.py", "raise SystemExit(0)\n"),
        (repo / "contract.yaml", "files:\n  - path: out.md\n    format: text\n"),
    ]


def test_proposed_contract_requires_the_root_yaml_and_strictly_parses_it(tmp_path) -> None:
    repo = tmp_path / "repo"
    targets = _targets(repo)

    missing = proposed_contract_errors(targets[:-1], repo)
    assert missing == [{"path": "contract.yaml", "error": "missing contract.yaml"}]

    malformed = [*targets[:-1], (repo / "contract.yaml", "files: [")]
    errors = proposed_contract_errors(malformed, repo)
    assert errors and all(error["path"] == "contract.yaml" for error in errors)
    assert not repo.exists()


def test_written_contract_rechecks_the_same_strict_birth_gate(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for path, content in _targets(repo):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert written_contract_errors(repo) == []

    (repo / "contract.yaml").unlink()
    assert {error["path"] for error in written_contract_errors(repo)} == {"contract.yaml"}


def test_contract_warning_tiers_keep_their_display_text(tmp_path) -> None:
    assert contract_tier_warnings(tmp_path) == (
        "no machine contract; runtime output validation is existence-only",
    )
    (tmp_path / "references").mkdir()
    (tmp_path / "references/output-contract.md").write_text(
        "```yaml contract\nfiles:\n  - path: out.md\n    format: text\n```\n",
        encoding="utf-8",
    )
    assert contract_tier_warnings(tmp_path) == (
        "machine contract lives in the deprecated embedded block; migrate to a root contract.yaml",
    )
    (tmp_path / "contract.yaml").write_text(
        "files:\n  - path: out.md\n    format: text\n", encoding="utf-8"
    )
    assert contract_tier_warnings(tmp_path) == ()
