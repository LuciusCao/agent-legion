"""Cross-product regression tests for the extracted root-contract parser."""

from pathlib import Path

import pytest

from server.app.services.skill_contract import contract_yaml_errors
from server.app.services.skill_contract_advisories import contract_warnings
from server.app.services.skill_edit_checks import (
    contract_warnings as legacy_contract_warnings,
)
from server.app.services.skill_edit_checks import (
    contract_yaml_errors as legacy_contract_yaml_errors,
)
from server.app.skills.contract_probe import probe_contract

pytestmark = pytest.mark.no_db


def _write_legacy(skill: Path, body: str) -> None:
    (skill / "references").mkdir()
    (skill / "references/output-contract.md").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("files: [", "invalid contract YAML"),
        ("files: []", "non-empty list"),
        ("files:\n  - path: ../escape.md\n    format: text\n", "must not contain"),
        ("files:\n  - path: out.json\n    format: json\n", "requires a `schema`"),
        ("files:\n  - path: out.md\n    format: text\n    unknown: true\n", "unknown field"),
    ],
)
def test_root_and_legacy_regressions_keep_their_validation_channel(
    tmp_path: Path, body: str, needle: str
) -> None:
    (tmp_path / "contract.yaml").write_text(body, encoding="utf-8")
    _write_legacy(
        tmp_path,
        "```yaml contract\nfiles:\n  - path: legacy.md\n    format: text\n```\n",
    )

    errors = contract_yaml_errors(tmp_path)

    assert errors and needle in errors[0]["error"]
    assert contract_warnings(tmp_path) == []
    assert probe_contract(tmp_path) == "root_yaml"


def test_valid_root_completely_ignores_a_malformed_legacy_block(tmp_path: Path) -> None:
    (tmp_path / "contract.yaml").write_text(
        "files:\n  - path: root.md\n    format: text\n", encoding="utf-8"
    )
    _write_legacy(tmp_path, "```yaml contract\nfiles: [\n")

    assert contract_yaml_errors(tmp_path) == []
    assert contract_warnings(tmp_path) == []
    assert probe_contract(tmp_path) == "root_yaml"


def test_malformed_legacy_block_remains_a_declared_deprecated_contract(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "```yaml contract\nfiles: [\n")

    assert contract_yaml_errors(tmp_path) == []
    assert probe_contract(tmp_path) == "embedded_block"
    assert contract_warnings(tmp_path) == [
        {
            "path": "contract.yaml",
            "error": "contract.yaml is missing; the machine contract still lives in the "
            "deprecated embedded block (migrate it to a root contract.yaml)",
        }
    ]


def test_existing_skill_edit_imports_remain_aliases_of_the_extracted_modules() -> None:
    assert legacy_contract_yaml_errors is contract_yaml_errors
    assert legacy_contract_warnings is contract_warnings
