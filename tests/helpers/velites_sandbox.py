"""Shared velites sandbox scaffolding for executor tests.

Used by the code-executor unit tests (``tests/executors/test_code_executor.py``),
the cancellation tests (``tests/executors/test_executor_cancellation.py``) and the
full-gate recovery evidence
(``tests/full/test_executor_cancellation_recovery.py``); kept here so those
suites do not import each other's test modules.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_DEBUG_BINARY = REPO_ROOT / "velites" / "target" / "debug" / "velites"


def velites_binary() -> Path:
    """Prebuilt debug binary, or a cargo build (skipped when cargo is absent)."""
    if VELITES_DEBUG_BINARY.exists():
        return VELITES_DEBUG_BINARY
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("no prebuilt velites binary and cargo is not available")
    proc = subprocess.run(
        [cargo, "build", "--manifest-path", str(REPO_ROOT / "velites" / "Cargo.toml")],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not VELITES_DEBUG_BINARY.exists():
        pytest.skip(f"velites build failed: {proc.stderr[-400:]}")
    return VELITES_DEBUG_BINARY


def sandbox_backend_available() -> bool:
    """Probe the actual OS sandbox backend, not just the platform."""
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    if sys.platform == "linux":
        return shutil.which("bwrap") is not None
    return False


def sandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    if not sandbox_backend_available():
        pytest.skip("no OS sandbox backend (macOS sandbox-exec / Linux bwrap)")
    binary = velites_binary()
    monkeypatch.setattr(
        "server.app.executors._code_sandbox.shutil.which", lambda _name: str(binary)
    )
