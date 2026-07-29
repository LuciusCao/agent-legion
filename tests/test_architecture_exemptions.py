"""Tests for the architecture exemption registry loader and validator."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from server.app.quality.exemptions import (
    ArchitectureExemption,
    load_exemptions,
    validate_exemptions,
)


@pytest.fixture
def exemptions_root(tmp_path: Path) -> Path:
    """Create a temporary project root with plan and issue files."""
    root = tmp_path / "project"
    (root / "docs" / "superpowers" / "plans").mkdir(parents=True)
    (root / "issues" / "open").mkdir(parents=True)
    (root / "issues" / "closed").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    (root / "docs" / "superpowers" / "plans" / "example.md").write_text("# Task 3\n")
    (root / "issues" / "open" / "123.md").write_text("issue\n")
    (root / "issues" / "closed" / "456.md").write_text("issue\n")

    return root


@pytest.fixture
def write_exemptions(exemptions_root: Path):
    """Write an exemptions YAML into the temporary root and return the loaded exemptions."""

    def _write(data: dict) -> tuple[ArchitectureExemption, ...]:
        path = exemptions_root / "config" / "architecture" / "architecture-exemptions.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data))
        return load_exemptions(path)

    return _write


def test_load_valid_exemptions(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                }
            ]
        }
    )
    assert len(exemptions) == 1
    ex = exemptions[0]
    assert ex.check == "architecture.import_boundary"
    assert ex.path == "server/app/example.py"
    assert ex.reason == "Specific technical reason."
    assert ex.owner == "workspace-executor"
    assert ex.remove_when == "docs/superpowers/plans/example.md#task-3"
    assert validate_exemptions(exemptions, exemptions_root) == []


def test_wildcard_only_path_rejected(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "*",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("wildcard" in e.lower() for e in errors)


@pytest.mark.parametrize(
    "vague_reason",
    [
        "legacy",
        "temporary",
        "follow up",
        "This is a legacy exemption.",
        "Just temporary until done.",
        "TODO: follow up later",
    ],
)
def test_vague_reason_rejected(write_exemptions, exemptions_root, vague_reason):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": vague_reason,
                    "owner": "workspace-executor",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("vague" in e.lower() for e in errors)


def test_missing_owner_rejected(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("owner" in e.lower() for e in errors)


def test_missing_remove_when_rejected(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "",
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("remove_when" in e.lower() for e in errors)


@pytest.mark.parametrize(
    "bad_remove_when",
    [
        "some/random/file.md#task-3",
        "docs/superpowers/specs/example.md#task-3",
        "docs/superpowers/plans/missing.md#task-3",
        "issues/draft/123.md",
        "issues/open/missing.md",
    ],
)
def test_untracked_remove_when_rejected(write_exemptions, exemptions_root, bad_remove_when):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": bad_remove_when,
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("remove_when" in e.lower() for e in errors)


def test_open_issue_accepted(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "issues/open/123.md",
                }
            ]
        }
    )
    assert validate_exemptions(exemptions, exemptions_root) == []


def test_closed_issue_accepted(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "issues/closed/456.md",
                }
            ]
        }
    )
    assert validate_exemptions(exemptions, exemptions_root) == []


def test_plan_section_with_anchor_accepted(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                }
            ]
        }
    )
    assert validate_exemptions(exemptions, exemptions_root) == []


def test_empty_exemptions_valid(write_exemptions, exemptions_root):
    exemptions = write_exemptions({"exemptions": []})
    assert exemptions == ()
    assert validate_exemptions(exemptions, exemptions_root) == []


def test_file_budget_requires_ceiling(write_exemptions, exemptions_root):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.file_budget",
                    "path": "server/app/example.py",
                    "reason": "Oversized module needs staged split.",
                    "owner": "video-hive",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("ceiling is required" in e.lower() for e in errors)


@pytest.mark.parametrize("bad_ceiling", [True, False, 0, -1, "100", 1.5])
def test_file_budget_rejects_invalid_ceiling(write_exemptions, exemptions_root, bad_ceiling):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.file_budget",
                    "path": "server/app/example.py",
                    "reason": "Oversized module needs staged split.",
                    "owner": "video-hive",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                    "ceiling": bad_ceiling,
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("ceiling must be a positive non-boolean integer" in e.lower() for e in errors)


def test_file_budget_ceiling_below_actual_rejected(write_exemptions, exemptions_root):
    (exemptions_root / "server" / "app").mkdir(parents=True)
    (exemptions_root / "server" / "app" / "example.py").write_text(
        "\n".join(["line"] * 100), encoding="utf-8"
    )
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.file_budget",
                    "path": "server/app/example.py",
                    "reason": "Oversized module needs staged split.",
                    "owner": "video-hive",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                    "ceiling": 50,
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("ceiling 50 is below actual file size" in e for e in errors)


@pytest.mark.parametrize(
    "check",
    [
        "architecture.route_response_model",
        "architecture.route_annotation_any",
        "architecture.route_import_boundary",
        "architecture.scheduler_import_boundary",
        "architecture.scheduler_threadpool",
        "architecture.import_boundary",
    ],
)
def test_non_file_budget_exemption_rejects_ceiling(write_exemptions, exemptions_root, check):
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": check,
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "video-hive",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                    "ceiling": 100,
                }
            ]
        }
    )
    errors = validate_exemptions(exemptions, exemptions_root)
    assert any("ceiling is only allowed for architecture.file_budget" in e for e in errors)


def test_file_budget_valid_ceiling_accepted(write_exemptions, exemptions_root):
    (exemptions_root / "server" / "app").mkdir(parents=True)
    (exemptions_root / "server" / "app" / "example.py").write_text(
        "\n".join(["line"] * 50), encoding="utf-8"
    )
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.file_budget",
                    "path": "server/app/example.py",
                    "reason": "Oversized module needs staged split.",
                    "owner": "video-hive",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                    "ceiling": 100,
                }
            ]
        }
    )
    assert validate_exemptions(exemptions, exemptions_root) == []


def test_missing_reference_accepted_when_internal_docs_not_published(
    write_exemptions, exemptions_root
):
    """Open-source checkouts do not ship docs/superpowers or issues/.

    When those directory trees are absent, the remove_when existence check is
    skipped so quality gates keep passing; prefix validation still applies.
    """
    shutil.rmtree(exemptions_root / "docs" / "superpowers")
    shutil.rmtree(exemptions_root / "issues")
    exemptions = write_exemptions(
        {
            "exemptions": [
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/example.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "docs/superpowers/plans/example.md#task-3",
                },
                {
                    "check": "architecture.import_boundary",
                    "path": "server/app/other.py",
                    "reason": "Specific technical reason.",
                    "owner": "workspace-executor",
                    "remove_when": "issues/open/123.md",
                },
            ]
        }
    )
    assert validate_exemptions(exemptions, exemptions_root) == []
