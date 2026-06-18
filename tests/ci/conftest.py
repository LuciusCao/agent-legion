"""Shared fixtures and marker registration for CI-extended architecture evidence."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the ci_extended marker for this directory."""
    config.addinivalue_line("markers", "ci_extended: repeated stress scenarios for CI")
