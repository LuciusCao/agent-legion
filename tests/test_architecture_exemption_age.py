"""Tests for exemption age warnings in server.app.quality.exemption_age."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from server.app.quality.exemption_age import exemption_age_warnings
from server.app.quality.exemptions import ArchitectureExemption, load_exemptions

TODAY = date(2026, 7, 22)


@pytest.fixture
def exemptions_root(tmp_path: Path) -> Path:
    """Create a temporary project root with dated plan and issue references."""
    root = tmp_path / "project"
    (root / "docs" / "superpowers" / "plans").mkdir(parents=True)
    (root / "issues" / "open").mkdir(parents=True)

    (root / "docs" / "superpowers" / "plans" / "2026-06-15-old-plan.md").write_text("# Plan\n")
    (root / "docs" / "superpowers" / "plans" / "2026-07-01-recent-plan.md").write_text("# Plan\n")
    (root / "docs" / "superpowers" / "plans" / "undated-plan.md").write_text("# Plan\n")
    (root / "issues" / "open" / "001-old-issue.md").write_text(
        "---\nstatus: open\nsource_review: 2026-06-10\n---\n\n# Issue\n"
    )
    (root / "issues" / "open" / "002-undated-issue.md").write_text(
        "---\nstatus: open\n---\n\n# Issue\n"
    )

    return root


def make_exemption(remove_when: str) -> ArchitectureExemption:
    return ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="video-hive",
        remove_when=remove_when,
        ceiling=100,
    )


def test_old_plan_reference_warns(exemptions_root: Path) -> None:
    warnings = exemption_age_warnings(
        (make_exemption("docs/superpowers/plans/2026-06-15-old-plan.md#task-1"),),
        exemptions_root,
        today=TODAY,
    )
    assert len(warnings) == 1
    assert "37 days old" in warnings[0]
    assert "architecture.file_budget" in warnings[0]
    assert "server/app/example.py" in warnings[0]


def test_recent_plan_reference_does_not_warn(exemptions_root: Path) -> None:
    warnings = exemption_age_warnings(
        (make_exemption("docs/superpowers/plans/2026-07-01-recent-plan.md"),),
        exemptions_root,
        today=TODAY,
    )
    assert warnings == []


def test_reference_exactly_at_age_limit_does_not_warn(exemptions_root: Path) -> None:
    warnings = exemption_age_warnings(
        (make_exemption("docs/superpowers/plans/2026-06-15-old-plan.md"),),
        exemptions_root,
        today=date(2026, 7, 15),
    )
    assert warnings == []


def test_issue_frontmatter_date_is_used(exemptions_root: Path) -> None:
    warnings = exemption_age_warnings(
        (make_exemption("issues/open/001-old-issue.md"),),
        exemptions_root,
        today=TODAY,
    )
    assert len(warnings) == 1
    assert "42 days old" in warnings[0]


def test_undated_references_are_skipped(exemptions_root: Path) -> None:
    warnings = exemption_age_warnings(
        (
            make_exemption("docs/superpowers/plans/undated-plan.md"),
            make_exemption("issues/open/002-undated-issue.md"),
            make_exemption("issues/open/missing.md"),
        ),
        exemptions_root,
        today=TODAY,
    )
    assert warnings == []


def test_custom_max_age_days(exemptions_root: Path) -> None:
    warnings = exemption_age_warnings(
        (make_exemption("docs/superpowers/plans/2026-07-01-recent-plan.md"),),
        exemptions_root,
        today=TODAY,
        max_age_days=10,
    )
    assert len(warnings) == 1
    assert "limit 10" in warnings[0]


def test_loaded_registry_roundtrip(exemptions_root: Path) -> None:
    registry = exemptions_root / "config" / "architecture-exemptions.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        yaml.safe_dump(
            {
                "exemptions": [
                    {
                        "check": "architecture.file_budget",
                        "path": "server/app/example.py",
                        "reason": "Oversized module needs staged split.",
                        "owner": "video-hive",
                        "remove_when": "docs/superpowers/plans/2026-06-15-old-plan.md",
                        "ceiling": 100,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    exemptions = load_exemptions(registry)
    warnings = exemption_age_warnings(exemptions, exemptions_root, today=TODAY)
    assert len(warnings) == 1
    assert warnings[0].startswith("exemption 1 (architecture.file_budget")
