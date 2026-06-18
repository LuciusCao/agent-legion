"""Temporary CI-extended smoke test for the architecture invariant registry.

Remove this file once Task 4 adds real ci_extended evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.quality.invariants import load_registry, validate_registry


@pytest.mark.ci_extended
def test_invariant_registry_loads() -> None:
    """Smoke test: the invariant registry loads and validates without errors."""
    root = Path(__file__).resolve().parents[2]
    registry_path = root / "config" / "architecture" / "architecture-invariants.yaml"
    invariants = load_registry(registry_path)
    errors = validate_registry(invariants, base_path=root)
    assert errors == []
