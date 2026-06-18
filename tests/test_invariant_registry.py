"""Tests for the architecture invariant registry loader and validator."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from server.app.quality.invariants import (
    GATE_PREFIXES,
    ArchitectureInvariant,
    InvariantEvidence,
    load_registry,
    validate_registry,
)


@pytest.fixture
def registry_root(tmp_path: Path) -> Path:
    """Create a temporary project root with gate directories and evidence targets."""
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "full").mkdir(parents=True)
    (root / "tests" / "ci").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)

    # Create valid evidence targets.
    (root / "tests" / "test_example.py").write_text(
        textwrap.dedent(
            """
            def test_local_pass():
                pass

            def test_full_pass():
                pass
            """
        ).strip()
    )
    (root / "tests" / "full" / "test_full.py").write_text(
        textwrap.dedent(
            """
            def test_full_runtime():
                pass
            """
        ).strip()
    )
    (root / "tests" / "ci" / "test_ci.py").write_text(
        textwrap.dedent(
            """
            def test_ci_runtime():
                pass
            """
        ).strip()
    )
    (root / "scripts" / "check_architecture.py").write_text("#!/usr/bin/env python3\n")
    (root / "scripts" / "generate-api-types.sh").write_text("#!/bin/sh\n")

    return root


@pytest.fixture
def write_registry(registry_root: Path):
    """Write a registry YAML into the temporary root and return the loaded invariants."""

    def _write(data: dict) -> tuple[ArchitectureInvariant, ...]:
        path = registry_root / "config" / "architecture" / "architecture-invariants.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data))
        return load_registry(path)

    return _write


def test_load_valid_registry(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Generated types match the backend schema.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/generate-api-types.sh",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    assert len(invariants) == 1
    inv = invariants[0]
    assert inv.id == "API-CONTRACT-001"
    assert inv.statement == "Generated types match the backend schema."
    assert inv.owner == "architecture"
    assert inv.risk == "high"
    assert inv.evidence == (
        InvariantEvidence(
            kind="contract",
            target="scripts/generate-api-types.sh",
            gate="quick",
        ),
    )
    assert validate_registry(invariants, registry_root) == []


def test_duplicate_ids_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "First.",
                    "owner": "a",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/generate-api-types.sh",
                            "gate": "quick",
                        }
                    ],
                },
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Second.",
                    "owner": "b",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                },
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("duplicate invariant ID" in e and "API-CONTRACT-001" in e for e in errors)


@pytest.mark.parametrize(
    "bad_id",
    [
        "INVALID-001",
        "API-contract-001",
        "API-CONTRACT-01",
        "api-CONTRACT-001",
        "API-CONTRACT-0001",
    ],
)
def test_invalid_id_prefix_rejected(write_registry, registry_root, bad_id):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": bad_id,
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("invalid ID format" in e for e in errors)


def test_missing_owner_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("owner is empty" in e for e in errors)


def test_missing_statement_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("statement is empty" in e for e in errors)


def test_unsupported_risk_level_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "medium",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("unsupported risk level" in e for e in errors)


def test_missing_evidence_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("missing evidence" in e for e in errors)


def test_critical_without_local_runtime_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "EXEC-RUNTIME-001",
                    "statement": "Executor leases are exclusive.",
                    "owner": "runtime",
                    "risk": "critical",
                    "evidence": [
                        {
                            "kind": "integration",
                            "target": "tests/full/test_full.py::test_full_runtime",
                            "gate": "full",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("local" in e.lower() for e in errors)


def test_critical_without_full_runtime_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "EXEC-RUNTIME-001",
                    "statement": "Executor leases are exclusive.",
                    "owner": "runtime",
                    "risk": "critical",
                    "evidence": [
                        {
                            "kind": "integration",
                            "target": "tests/test_example.py::test_local_pass",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("full" in e.lower() for e in errors)


def test_critical_ci_never_satisfies(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "EXEC-RUNTIME-001",
                    "statement": "Executor leases are exclusive.",
                    "owner": "runtime",
                    "risk": "critical",
                    "evidence": [
                        {
                            "kind": "multiprocess",
                            "target": "tests/test_example.py::test_local_pass",
                            "gate": "quick",
                        },
                        {
                            "kind": "failure_injection",
                            "target": "tests/ci/test_ci.py::test_ci_runtime",
                            "gate": "ci_extended",
                        },
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("full" in e.lower() for e in errors)


def test_evidence_target_outside_gate_directory(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/generate-api-types.sh",
                            "gate": "full",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("outside gate" in e for e in errors)


def test_unsupported_evidence_kind_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "benchmark",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("unsupported evidence kind" in e for e in errors)


def test_missing_evidence_target_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "contract",
                            "target": "scripts/missing.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("does not exist" in e for e in errors)


def test_test_symbol_not_found_rejected(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "integration",
                            "target": "tests/test_example.py::test_missing",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert any("symbol" in e.lower() for e in errors)


def test_high_risk_static_only_allowed(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "API-CONTRACT-001",
                    "statement": "Statement.",
                    "owner": "architecture",
                    "risk": "high",
                    "evidence": [
                        {
                            "kind": "static",
                            "target": "scripts/check_architecture.py",
                            "gate": "quick",
                        }
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert errors == []


def test_critical_with_local_and_full_runtime_passes(write_registry, registry_root):
    invariants = write_registry(
        {
            "invariants": [
                {
                    "id": "EXEC-RUNTIME-001",
                    "statement": "Executor leases are exclusive.",
                    "owner": "runtime",
                    "risk": "critical",
                    "evidence": [
                        {
                            "kind": "integration",
                            "target": "tests/test_example.py::test_local_pass",
                            "gate": "quick",
                        },
                        {
                            "kind": "integration",
                            "target": "tests/full/test_full.py::test_full_runtime",
                            "gate": "full",
                        },
                    ],
                }
            ]
        }
    )
    errors = validate_registry(invariants, base_path=registry_root)
    assert errors == []


def test_gate_prefixes_constant_shape():
    assert set(GATE_PREFIXES) == {"quick", "full", "ci_extended"}
    assert GATE_PREFIXES["quick"] == ("tests/", "scripts/")
    assert GATE_PREFIXES["full"] == ("tests/full/",)
    assert GATE_PREFIXES["ci_extended"] == ("tests/ci/",)
