"""Integration tests for the velites OS-level filesystem sandbox (M4.5).

Evidence for EXEC-HARNESS-SANDBOX-001 (design doc §5 沙箱小节):

- fail-closed: when the sandbox backend is unavailable (simulated with an
  emptied PATH so neither sandbox-exec nor bwrap can be probed), the harness
  exits non-zero BEFORE the agent loop — it never degrades to an unsandboxed
  run; ``--no-sandbox`` is the only escape hatch;
- macOS seatbelt: a stub-provider session drives the bash tool against
  ``$HOME`` (file contents unreadable and writes denied; ancestor dirs of
  whitelist roots may be list-only, names only), the job dir (read/write
  allowed), the session dir (write allowed), and a ``--skill`` dir
  (read-only).

The Linux bubblewrap path is covered by the Rust tests (argv unit tests in
``velites/src/sandbox.rs`` plus a bwrap-gated integration test in
``velites/tests/os_sandbox.rs``); CI's Linux lane has no bwrap, so the
confinement test here is macOS-only while the fail-closed semantics are
platform-independent.

These tests need the debug binary. When neither ``velites/target/debug/velites``
nor a ``cargo`` toolchain is available the module skips (quick lane must pass
on machines without Rust).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_DIR = REPO_ROOT / "velites"
BINARY = VELITES_DIR / "target" / "debug" / "velites"

if not BINARY.exists() and shutil.which("cargo") is None:
    pytest.skip(
        "velites binary not built and cargo unavailable; skipping sandbox tests",
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


def _write_fixture(workdir: Path, responses: list[dict[str, Any]]) -> Path:
    fixture = workdir / "fixture.json"
    fixture.write_text(json.dumps({"responses": responses}), encoding="utf-8")
    return fixture


def _run(
    binary: Path,
    workdir: Path,
    args: list[str],
    *,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(binary), *args],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _events(proc: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _read_only_fixture_args(tmp_path: Path) -> list[str]:
    """Stub session that only uses the read tool (never spawns bash, so an
    emptied PATH cannot break an otherwise successful run)."""
    (tmp_path / "prompt.md").write_text("Hi.", encoding="utf-8")
    fixture = _write_fixture(
        tmp_path,
        [
            {"content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}]},
            {"content": [{"type": "text", "text": "done"}]},
        ],
    )
    return [
        "--mode",
        "json",
        "--provider",
        "stub",
        "--stub-fixture",
        str(fixture),
        "@prompt.md",
    ]


def test_sandbox_unavailable_fails_closed(tmp_path: Path, velites_binary: Path) -> None:
    """EXEC-HARNESS-SANDBOX-001: unavailable backend -> exit != 0, no agent loop."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    workdir = tmp_path / "job"
    workdir.mkdir()
    proc = _run(
        velites_binary,
        workdir,
        _read_only_fixture_args(workdir),
        env_extra={"PATH": str(empty_bin)},
    )
    assert proc.returncode != 0, "sandbox-unavailable run must fail closed"
    assert "sandbox" in proc.stderr, proc.stderr
    assert "--no-sandbox" in proc.stderr, proc.stderr
    # Fail-closed means BEFORE the agent loop: no events were emitted.
    assert "agent_start" not in proc.stdout


def test_no_sandbox_escape_hatch(tmp_path: Path, velites_binary: Path) -> None:
    """EXEC-HARNESS-SANDBOX-001: --no-sandbox bypasses the startup probe."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    workdir = tmp_path / "job"
    workdir.mkdir()
    proc = _run(
        velites_binary,
        workdir,
        [*_read_only_fixture_args(workdir), "--no-sandbox"],
        env_extra={"PATH": str(empty_bin)},
    )
    assert proc.returncode == 0, proc.stderr
    events = _events(proc)
    assert events[-1]["type"] == "agent_end"


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt backend is macOS-only")
def test_bash_sandbox_blocks_escape_macos(tmp_path: Path, velites_binary: Path) -> None:
    """EXEC-HARNESS-SANDBOX-001: seatbelt denies $HOME contents/writes, allows job/session/skill."""
    job = tmp_path / "job"
    skill = tmp_path / "skill"
    session = tmp_path / "session"
    job.mkdir()
    skill.mkdir()
    (job / "prompt.md").write_text("Run the commands.", encoding="utf-8")
    (skill / "SKILL.md").write_text("You are a test skill.", encoding="utf-8")
    probe = Path.home() / ".velites_sandbox_probe"
    read_probe = Path.home() / ".velites_sandbox_read_probe"
    read_probe.write_text("secret", encoding="utf-8")

    commands = [
        # File contents outside the allowed roots ($HOME) stay unreadable.
        # ($HOME itself may be *listable*: whitelist roots such as a uv python
        # under ~/.local get list-only ancestor grants so agents can see the
        # roots exist — names only, never file contents.)
        f"cat '{read_probe}'",
        # Writing outside the allowed roots is denied.
        f"echo pwned > '{probe}'",
        # Read/write inside the job dir works.
        "echo hi > ok.txt && cat ok.txt",
        # The --skill dir stays readable.
        f"cat '{skill / 'SKILL.md'}'",
        # The session dir is writable.
        f"echo session-ok > '{session / 'extra.txt'}'",
    ]
    fixture = _write_fixture(
        job,
        [
            {
                "content": [
                    {"type": "toolCall", "name": "bash", "arguments": {"command": command}}
                    for command in commands
                ]
            },
            {"content": [{"type": "text", "text": "done"}]},
        ],
    )
    proc = _run(
        velites_binary,
        job,
        [
            "--mode",
            "json",
            "--provider",
            "stub",
            "--stub-fixture",
            str(fixture),
            "--session-dir",
            str(session),
            "--skill",
            str(skill),
            "@prompt.md",
        ],
    )
    leaked = probe.exists()
    if leaked:  # a regression let the write through; clean up before failing
        probe.unlink()
    read_probe.unlink(missing_ok=True)

    assert proc.returncode == 0, proc.stderr
    errors = [e["isError"] for e in _events(proc) if e["type"] == "tool_execution_end"]
    assert errors == [True, True, False, False, False], proc.stdout
    assert not leaked, "sandbox let a write escape into $HOME"
    assert (job / "ok.txt").read_text(encoding="utf-8").strip() == "hi"
    assert (session / "extra.txt").exists()
