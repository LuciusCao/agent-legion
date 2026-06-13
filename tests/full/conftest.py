"""Shared fixtures and marker registration for full-gate architecture evidence."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the full_gate marker for this directory."""
    config.addinivalue_line(
        "markers", "full_gate: deterministic higher-fidelity architecture scenarios"
    )
