"""Examples skills carry a machine contract (#542 CI regression).

Every ``examples/skills/*`` must ship a root ``contract.yaml`` that parses
and passes the platform's strict structure validation
(``services/skill_edit_checks.contract_yaml_errors``) — the same rules
``create_skill`` enforces at birth and the velites engine enforces at run
time. A broken example contract would silently degrade imported demo
workflows to existence-only validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.services.skill_edit_checks import contract_warnings, contract_yaml_errors
from server.app.skills.contract_probe import probe_contract

pytestmark = pytest.mark.no_db

_EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "skills"


def _example_skills() -> list[Path]:
    assert _EXAMPLES_ROOT.is_dir(), f"missing examples root: {_EXAMPLES_ROOT}"
    return sorted(child for child in _EXAMPLES_ROOT.iterdir() if child.is_dir())


def test_examples_root_lists_the_four_demo_skills() -> None:
    assert [skill.name for skill in _example_skills()] == [
        "generate-questions",
        "review-questions",
        "review-script",
        "write-script",
    ]


def test_every_example_skill_has_a_parseable_contract_yaml() -> None:
    for skill in _example_skills():
        assert probe_contract(skill) == "root_yaml", skill.name
        errors = contract_yaml_errors(skill)
        assert errors == [], f"{skill.name}: {errors}"
        assert contract_warnings(skill) == [], skill.name


def test_no_example_keeps_the_deprecated_embedded_block() -> None:
    """The migration must be complete: an embedded block next to a root
    contract.yaml would be dead weight (root wins completely) and a
    tripwire for future contract edits going to the wrong place."""
    for skill in _example_skills():
        doc = skill / "references" / "output-contract.md"
        assert doc.is_file(), f"{skill.name}: missing output-contract.md prose"
        content = doc.read_text(encoding="utf-8")
        assert "```yaml contract" not in content, skill.name
        # The prose points readers at the machine contract's new home.
        assert "contract.yaml" in content, skill.name
