"""Integration tests for budget policy handling in check_invariants."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import check_invariants

pytestmark = pytest.mark.no_db


def _registry_paths(root: Path) -> tuple[Path, Path]:
    registry = root / "invariants.yaml"
    registry.write_text("invariants: []\n", encoding="utf-8")
    exemptions = root / "exemptions.yaml"
    exemptions.write_text("exemptions: []\n", encoding="utf-8")
    return registry, exemptions


def _write_policy(root: Path) -> None:
    config = root / "config/architecture"
    config.mkdir(parents=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n"
        "  roots: []\n"
        "  exclude: []\n"
        "  buffer_lines: 10\n"
        "  max_lines: 800\n"
        "  growth_allowance: 15\n"
        "tests:\n"
        "  roots: []\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )


def test_missing_budget_policy_keeps_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    registry, exemptions = _registry_paths(tmp_path)
    monkeypatch.setattr(check_invariants, "project_root", tmp_path)

    with caplog.at_level(logging.ERROR):
        result = check_invariants.main(
            ["--registry", str(registry), "--exemptions", str(exemptions)]
        )

    assert result == 1
    assert caplog.messages == [
        "budget configuration: Policy file not found: "
        f"{tmp_path / 'config/architecture/architecture-budget-policy.yaml'}"
    ]


def test_expired_exemption_reaches_main_with_exact_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    registry, exemptions = _registry_paths(tmp_path)
    exemptions.write_text(
        "exemptions:\n"
        "- check: architecture.file_budget\n"
        "  path: server/app/example.py\n"
        "  reason: Oversized module needs staged split.\n"
        "  owner: agent-legion\n"
        "  remove_when: issues/open/001.md\n"
        "  ceiling: 100\n"
        "  expires: 2000-01-01\n",
        encoding="utf-8",
    )
    _write_policy(tmp_path)
    monkeypatch.setattr(check_invariants, "project_root", tmp_path)

    with caplog.at_level(logging.ERROR):
        result = check_invariants.main(
            ["--registry", str(registry), "--exemptions", str(exemptions)]
        )

    assert result == 1
    assert caplog.messages == [
        "exemption 1: exemption expired on 2000-01-01 — renew it with a fresh "
        "justification and a later expires date, or shrink the file back under its ceiling"
    ]


def test_main_uses_policy_growth_allowance_for_exemption_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    registry, exemptions = _registry_paths(tmp_path)
    source = tmp_path / "server/app/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"x_{idx} = {idx}" for idx in range(60)), encoding="utf-8")
    exemptions.write_text(
        "exemptions:\n"
        "- check: architecture.file_budget\n"
        "  path: server/app/example.py\n"
        "  reason: Oversized module needs staged split.\n"
        "  owner: agent-legion\n"
        "  remove_when: issues/open/001.md\n"
        "  ceiling: 50\n",
        encoding="utf-8",
    )
    _write_policy(tmp_path)
    monkeypatch.setattr(check_invariants, "project_root", tmp_path)

    with caplog.at_level(logging.INFO):
        result = check_invariants.main(
            ["--registry", str(registry), "--exemptions", str(exemptions)]
        )

    assert result == 0
    assert caplog.messages == ["OK: 0 architecture invariant(s) and 1 exemption(s) validated"]
