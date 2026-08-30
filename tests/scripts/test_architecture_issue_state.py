"""Tests for exemption expiry detection in scripts.quality.issue_state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.quality.exemptions import ArchitectureExemption, load_exemptions
from scripts.quality.issue_state import (
    IssueStateCacheError,
    expired_issue_errors,
    load_issue_states,
    parse_issue_reference,
)

pytestmark = pytest.mark.no_db

REFERENCE = "github.com/LuciusCao/agent-legion/issues/195"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a temporary project root with the manifest directory."""
    root = tmp_path / "project"
    (root / "config" / "architecture").mkdir(parents=True)
    return root


def write_manifest(root: Path, states: dict[str, str]) -> Path:
    manifest = root / "config" / "architecture" / "issue-states.json"
    manifest.write_text(
        json.dumps({"version": 1, "updated_at": "2026-08-30T00:00:00+00:00", "issues": states}),
        encoding="utf-8",
    )
    return manifest


def make_exemption(remove_when: str) -> ArchitectureExemption:
    return ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="agent-legion",
        remove_when=remove_when,
        ceiling=100,
    )


def test_parse_issue_reference_extracts_declared_state_and_url() -> None:
    assert parse_issue_reference(f"issues/open/{REFERENCE}") == ("open", REFERENCE)
    assert parse_issue_reference(f"issues/closed/{REFERENCE}") == ("closed", REFERENCE)


def test_parse_issue_reference_rejects_other_forms() -> None:
    for remove_when in (
        "docs/architecture/tracked-plan.md",
        "docs/superpowers/plans/2026-08-01-plan.md#task-1",
        "issues/open/123.md",
        "issues/open/github.com/LuciusCao/agent-legion/pulls/5",
        "issues/open/github.com/LuciusCao/agent-legion/issues/not-a-number",
        "issues/draft/github.com/LuciusCao/agent-legion/issues/195",
    ):
        assert parse_issue_reference(remove_when) is None


def test_cache_manifest_declared_closed_fails(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "closed"})
    errors = expired_issue_errors((make_exemption(f"issues/open/{REFERENCE}"),), project_root)
    assert len(errors) == 1
    assert "exemption expired" in errors[0]
    assert REFERENCE in errors[0]
    assert "issues/open/" in errors[0]


def test_cache_manifest_declared_open_passes(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "open"})
    assert expired_issue_errors((make_exemption(f"issues/open/{REFERENCE}"),), project_root) == []


def test_issue_missing_from_cache_is_treated_as_open(project_root: Path) -> None:
    """The cache only lists issues referenced when it was last refreshed.

    A stale cache must not expire a newly added exemption: unknown means
    not-proven-closed, matching the offline deterministic contract.
    """
    write_manifest(project_root, {"github.com/LuciusCao/agent-legion/issues/999": "open"})
    assert expired_issue_errors((make_exemption(f"issues/open/{REFERENCE}"),), project_root) == []


def test_missing_cache_manifest_keeps_issue_exemptions_unexpired(project_root: Path) -> None:
    """No manifest (open-source checkout without the refresh tool): pass-through.

    The self-declared ``issues/closed/`` prefix still fails, because that
    declaration needs no cache.
    """
    assert expired_issue_errors((make_exemption(f"issues/open/{REFERENCE}"),), project_root) == []
    errors = expired_issue_errors((make_exemption(f"issues/closed/{REFERENCE}"),), project_root)
    assert len(errors) == 1
    assert "exemption expired" in errors[0]


def test_plan_and_local_issue_references_are_ignored(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "closed"})
    exemptions = (
        make_exemption("docs/architecture/tracked-plan.md"),
        make_exemption("docs/superpowers/plans/2026-08-01-plan.md#task-1"),
        make_exemption("issues/open/123.md"),
    )
    assert expired_issue_errors(exemptions, project_root) == []


def test_error_message_identifies_the_exemption(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "closed"})
    exemptions = (
        make_exemption("docs/architecture/tracked-plan.md"),
        make_exemption(f"issues/open/{REFERENCE}"),
    )
    errors = expired_issue_errors(exemptions, project_root)
    assert len(errors) == 1
    assert errors[0].startswith("exemption 2 (architecture.file_budget on server/app/example.py)")


def test_multiple_closed_anchors_all_reported(project_root: Path) -> None:
    other = "github.com/LuciusCao/agent-legion/issues/160"
    write_manifest(project_root, {REFERENCE: "closed", other: "closed"})
    errors = expired_issue_errors(
        (
            make_exemption(f"issues/open/{REFERENCE}"),
            make_exemption(f"issues/open/{other}"),
        ),
        project_root,
    )
    assert len(errors) == 2


def test_non_file_budget_checks_are_also_governed(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "closed"})
    exemption = ArchitectureExemption(
        check="architecture.route_response_model",
        path="server/app/routes/example.py:handler",
        reason="Streaming response has no JSON model.",
        owner="workspace-executor",
        remove_when=f"issues/open/{REFERENCE}",
    )
    errors = expired_issue_errors((exemption,), project_root)
    assert len(errors) == 1
    assert "exemption expired" in errors[0]


def test_loaded_registry_roundtrip(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "closed"})
    registry = project_root / "config" / "architecture" / "architecture-exemptions.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "exemptions": [
                    {
                        "check": "architecture.file_budget",
                        "path": "server/app/example.py",
                        "reason": "Oversized module needs staged split.",
                        "owner": "agent-legion",
                        "remove_when": f"issues/open/{REFERENCE}",
                        "ceiling": 100,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    errors = expired_issue_errors(load_exemptions(registry), project_root)
    assert len(errors) == 1
    assert "exemption 1" in errors[0]


def test_load_issue_states_returns_reference_to_state_map(project_root: Path) -> None:
    write_manifest(project_root, {REFERENCE: "closed", "github.com/o/r/issues/7": "open"})
    states = load_issue_states(project_root / "config" / "architecture" / "issue-states.json")
    assert states == {REFERENCE: "closed", "github.com/o/r/issues/7": "open"}


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        json.dumps({"version": 1, "issues": []}),
        json.dumps({"version": 2, "issues": {"github.com/o/r/issues/1": "open"}}),
        json.dumps({"version": 1, "issues": {"github.com/o/r/issues/1": "reopened"}}),
        json.dumps({"version": 1, "issues": {"": "open"}}),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "issues": {"a": "open"}, "extra": 1}),
        "{not json",
    ],
)
def test_malformed_cache_manifest_fails_the_check(project_root: Path, payload: str) -> None:
    """A malformed tracked manifest is a red, not a silent pass-through."""
    manifest = project_root / "config" / "architecture" / "issue-states.json"
    manifest.write_text(payload, encoding="utf-8")
    with pytest.raises(IssueStateCacheError):
        load_issue_states(manifest)
    errors = expired_issue_errors((make_exemption(f"issues/open/{REFERENCE}"),), project_root)
    assert len(errors) == 1
    assert "issue-states.json" in errors[0]
