"""Focused regression tests for exemption schema parsing and calendar expiry."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from scripts.quality import exemption_schema, exemptions
from scripts.quality.exemption_schema import ArchitectureExemption, load_exemptions
from scripts.quality.exemptions import validate_exemptions

pytestmark = pytest.mark.no_db


def _exemption(**overrides: object) -> ArchitectureExemption:
    values = {
        "check": "architecture.file_budget",
        "path": "scripts/quality/example.py",
        "reason": "Oversized module needs staged split.",
        "owner": "workspace-executor",
        "remove_when": "docs/architecture/removal-plan.md",
        "ceiling": 100,
        "expires": "2026-12-31",
    }
    return ArchitectureExemption(**{**values, **overrides})  # type: ignore[arg-type]


def test_existing_import_surface_reexports_schema_and_loader() -> None:
    assert exemptions.ArchitectureExemption is exemption_schema.ArchitectureExemption
    assert exemptions.load_exemptions is exemption_schema.load_exemptions


def test_unquoted_yaml_date_is_normalized_with_the_rest_of_the_schema(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "architecture-exemptions.yaml"
    registry.write_text(
        "exemptions:\n"
        "- check: architecture.file_budget\n"
        "  path: scripts/quality/example.py\n"
        "  reason: Oversized module needs staged split.\n"
        "  owner: workspace-executor\n"
        "  remove_when: docs/architecture/removal-plan.md\n"
        "  ceiling: 100\n"
        "  expires: 2026-12-31\n",
        encoding="utf-8",
    )

    assert load_exemptions(registry) == (_exemption(),)


def test_expiry_on_today_remains_valid(tmp_path: Path) -> None:
    exemption = _exemption(expires="2026-09-13")

    assert validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13)) == []


def test_past_expiry_preserves_the_exact_renewal_error(tmp_path: Path) -> None:
    exemption = _exemption(expires="2026-09-01")

    assert validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13)) == [
        "exemption 1: exemption expired on 2026-09-01 — renew it with a fresh "
        "justification and a later expires date, or shrink the file back under its ceiling"
    ]


@pytest.mark.parametrize(
    ("expires", "expected"),
    [
        (12345, "expires 12345 must be an ISO date string (YYYY-MM-DD)"),
        ("2026-9-13", "expires '2026-9-13' must be an ISO date (YYYY-MM-DD)"),
        ("not-a-date", "expires 'not-a-date' must be an ISO date (YYYY-MM-DD)"),
    ],
)
def test_invalid_expiry_preserves_validation_errors(
    tmp_path: Path, expires: object, expected: str
) -> None:
    exemption = replace(_exemption(), expires=expires)  # type: ignore[arg-type]

    assert validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13)) == [
        f"exemption 1: {expected}"
    ]
