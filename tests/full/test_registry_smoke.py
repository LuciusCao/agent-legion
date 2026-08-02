"""Temporary full-gate smoke test for the architecture invariant registry.

Remove this file once Task 4 adds real full-gate evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.quality.invariants import load_registry, validate_registry


@pytest.mark.full_gate
def test_invariant_registry_loads() -> None:
    """Smoke test: the invariant registry loads and validates without errors."""
    root = Path(__file__).resolve().parents[2]
    registry_path = root / "config" / "architecture" / "architecture-invariants.yaml"
    invariants = load_registry(registry_path)
    errors = validate_registry(invariants, base_path=root)
    assert errors == []
