"""Integration test for the velites bash command guard (EXEC-HARNESS-GUARD-001).

Drives the real ``velites`` binary with the stub provider: when the model
emits a full-disk scan (``find / -name python3`` — the pattern that flooded
fseventsd under parallel agent load), the bash tool must reject it before
spawn and return remediation guidance the model can act on; scoped commands
still run.

Needs the debug binary (same skip policy as test_velites_controllability.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_DIR = REPO_ROOT / "velites"
BINARY = VELITES_DIR / "target" / "debug" / "velites"

if not BINARY.exists() and shutil.which("cargo") is None:
    pytest.skip(
        "velites binary not built and cargo unavailable; skipping bash guard tests",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def velites_binary() -> Path:
    if not BINARY.exists():
        subprocess.run(
            ["cargo", "build"],
            cwd=VELITES_DIR,
            check=True,
            timeout=900,
            capture_output=True,
        )
    assert BINARY.exists(), f"velites binary missing after build: {BINARY}"
    return BINARY


def _run_with_fixture(
    binary: Path, workdir: Path, responses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    (workdir / "prompt.md").write_text("Do the thing.", encoding="utf-8")
    fixture = workdir / "fixture.json"
    fixture.write_text(json.dumps({"responses": responses}), encoding="utf-8")
    # --no-sandbox: the guard applies with or without the OS sandbox, and CI's
    # Linux lane has no bubblewrap (confinement itself: test_velites_sandbox.py).
    proc = subprocess.run(
        [
            str(binary),
            "--mode",
            "json",
            "--provider",
            "stub",
            "--stub-fixture",
            str(fixture),
            "--session-dir",
            str(workdir / "session"),
            "--no-sandbox",
            "@prompt.md",
        ],
        cwd=workdir,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _bash_results(events: list[dict[str, Any]]) -> list[str]:
    return [
        json.dumps(event["result"])
        for event in events
        if event["type"] == "tool_execution_end" and event["toolName"] == "bash"
    ]


def test_full_disk_scan_blocked_with_guidance(tmp_path: Path, velites_binary: Path) -> None:
    """EXEC-HARNESS-GUARD-001: `find /` never spawns; the model gets guidance."""
    events = _run_with_fixture(
        velites_binary,
        tmp_path,
        [
            {
                "content": [
                    {
                        "type": "toolCall",
                        "name": "bash",
                        "arguments": {
                            "command": "find / -name python3 -type f 2>/dev/null | head -10"
                        },
                    }
                ]
            },
            {"content": [{"type": "text", "text": "ok"}]},
        ],
    )
    results = _bash_results(events)
    assert len(results) == 1, f"expected one bash result: {results}"
    assert "blocked by velites guard" in results[0]
    # Remediation guidance reaches the model.
    assert "command -v" in results[0]


def test_cd_evasion_blocked(tmp_path: Path, velites_binary: Path) -> None:
    """`cd / && find .` scans the same root via the shell cwd — also blocked."""
    events = _run_with_fixture(
        velites_binary,
        tmp_path,
        [
            {
                "content": [
                    {
                        "type": "toolCall",
                        "name": "bash",
                        "arguments": {"command": "cd / && find . -name python3"},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "ok"}]},
        ],
    )
    results = _bash_results(events)
    assert len(results) == 1, f"expected one bash result: {results}"
    assert "blocked by velites guard" in results[0]


def test_scoped_command_still_runs(tmp_path: Path, velites_binary: Path) -> None:
    """The guard must not break normal scoped commands."""
    events = _run_with_fixture(
        velites_binary,
        tmp_path,
        [
            {
                "content": [
                    {
                        "type": "toolCall",
                        "name": "bash",
                        "arguments": {"command": "echo guard-ok"},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "ok"}]},
        ],
    )
    results = _bash_results(events)
    assert len(results) == 1, f"expected one bash result: {results}"
    assert "guard-ok" in results[0]
    assert "blocked by velites guard" not in results[0]
