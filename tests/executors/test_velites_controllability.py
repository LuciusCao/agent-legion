"""Integration tests for velites M3 controllability (design doc §5).

Drives the real ``velites`` binary as a subprocess with the stub provider
(and, for the zero-auto-discovery invariant, a local mock SSE server):

- budget exhaustion ends the run with ``agent_end{reason: "budget_exceeded"}``
  after one wrap-up turn;
- SIGTERM ends the run promptly with ``agent_end{reason: "cancelled"}`` and
  exit code 0 (cancellation is a Host action, not a harness fault);
- ``--require-output`` triggers one remediation turn and always emits
  ``outputs_validation{missing: [...]}``;
- zero auto-discovery: an AGENTS.md in the cwd never reaches the provider
  request or the event stream.

These tests need the debug binary. When neither ``velites/target/debug/velites``
nor a ``cargo`` toolchain is available the module skips (quick lane must pass
on machines without Rust).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_DIR = REPO_ROOT / "velites"
BINARY = VELITES_DIR / "target" / "debug" / "velites"

if not BINARY.exists() and shutil.which("cargo") is None:
    pytest.skip(
        "velites binary not built and cargo unavailable; skipping controllability tests",
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


def _base_args(workdir: Path, fixture: Path) -> list[str]:
    return [
        "--mode",
        "json",
        "--provider",
        "stub",
        "--stub-fixture",
        str(fixture),
        "--session-dir",
        str(workdir / "session"),
        # These tests cover controllability, not filesystem confinement.
        # CI runs on Linux without bubblewrap, where the default-on OS
        # sandbox fails closed at startup; confinement itself is covered by
        # test_velites_sandbox.py.
        "--no-sandbox",
        "@prompt.md",
    ]


def _run(
    binary: Path,
    workdir: Path,
    args: list[str],
    *,
    env_extra: dict[str, str] | None = None,
    timeout: float = 30,
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
        timeout=timeout,
    )


def _events(proc: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


def test_budget_exhaustion_marks_agent_end(tmp_path: Path, velites_binary: Path) -> None:
    """EXEC-HARNESS-BUDGET-001: budget exhaustion ends with a marked agent_end."""
    (tmp_path / "prompt.md").write_text("Loop forever.", encoding="utf-8")
    fixture = _write_fixture(
        tmp_path,
        [
            {"content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}]},
            {"content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}]},
            {"content": [{"type": "text", "text": "wrapping up"}]},
        ],
    )
    proc = _run(
        velites_binary,
        tmp_path,
        [*_base_args(tmp_path, fixture), "--max-turns", "2"],
    )
    assert proc.returncode == 0, proc.stderr
    events = _events(proc)
    # Two budgeted turns + exactly one wrap-up turn.
    assert _event_types(events).count("turn_start") == 3
    agent_end = events[-1]
    assert agent_end["type"] == "agent_end"
    assert agent_end["reason"] == "budget_exceeded"
    # Schema v2: agent_end carries no message history.
    assert "messages" not in agent_end
    # The wrap-up notice was injected as a user message (session mirror).
    session_lines = (
        (tmp_path / "session" / "session.jsonl").read_text(encoding="utf-8").splitlines()
    )
    notices = [
        m
        for m in (json.loads(line) for line in session_lines if line.strip())
        if m["role"] == "user" and "--max-turns" in m["content"][0].get("text", "")
    ]
    assert notices, "budget wrap-up notice missing from session history"


def test_sigterm_cancel_emits_agent_end_cancelled(tmp_path: Path, velites_binary: Path) -> None:
    """SIGTERM during a bash tool call: graceful wrap-up, exit 0, cancelled reason."""
    (tmp_path / "prompt.md").write_text("Sleep.", encoding="utf-8")
    fixture = _write_fixture(
        tmp_path,
        [
            {
                "content": [
                    {"type": "toolCall", "name": "bash", "arguments": {"command": "sleep 60"}}
                ]
            },
            {"content": [{"type": "text", "text": "unreachable"}]},
        ],
    )
    proc = subprocess.Popen(
        [str(velites_binary), *_base_args(tmp_path, fixture)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    lines: list[str] = []

    def _pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()

    # Wait until the bash tool is actually running, then cancel.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any('"tool_execution_start"' in line for line in lines):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    assert proc.poll() is None, f"velites exited before cancel: {lines}"

    sent_at = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=20)
    elapsed = time.monotonic() - sent_at
    pump.join(timeout=5)
    stderr = proc.stderr.read() if proc.stderr is not None else ""

    # Cancellation is a Host action, not a harness fault: exit 0, promptly
    # (bash child gets TERM → grace → KILL, bounded by the 3s grace period).
    assert proc.returncode == 0, f"cancelled run must exit 0: {stderr}"
    assert elapsed < 15, f"cancel took too long: {elapsed:.1f}s"

    events = [json.loads(line) for line in lines if line.strip()]
    agent_end = events[-1]
    assert agent_end["type"] == "agent_end"
    assert agent_end["reason"] == "cancelled"


def test_require_output_remediation_and_validation(tmp_path: Path, velites_binary: Path) -> None:
    """Missing declared artifact: one remediation turn, then missing == []."""
    (tmp_path / "prompt.md").write_text("Produce result.txt.", encoding="utf-8")
    fixture = _write_fixture(
        tmp_path,
        [
            {"content": [{"type": "text", "text": "done without writing"}]},
            {
                "content": [
                    {
                        "type": "toolCall",
                        "name": "write",
                        "arguments": {"path": "result.txt", "content": "payload"},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "written"}]},
        ],
    )
    proc = _run(
        velites_binary,
        tmp_path,
        [*_base_args(tmp_path, fixture), "--require-output", "result.txt"],
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "payload"
    events = _events(proc)
    validations = [e for e in events if e["type"] == "outputs_validation"]
    assert len(validations) == 1
    assert validations[0]["missing"] == []
    assert events[-1]["type"] == "agent_end"
    assert "reason" not in events[-1]


def test_require_output_still_missing_reports_validation(
    tmp_path: Path, velites_binary: Path
) -> None:
    """Remediation did not produce the artifact: outputs_validation lists it."""
    (tmp_path / "prompt.md").write_text("Produce result.txt.", encoding="utf-8")
    fixture = _write_fixture(
        tmp_path,
        [
            {"content": [{"type": "text", "text": "nothing"}]},
            {"content": [{"type": "text", "text": "still nothing"}]},
        ],
    )
    proc = _run(
        velites_binary,
        tmp_path,
        [*_base_args(tmp_path, fixture), "--require-output", "result.txt"],
    )
    assert proc.returncode == 0, proc.stderr
    events = _events(proc)
    validations = [e for e in events if e["type"] == "outputs_validation"]
    assert len(validations) == 1
    assert validations[0]["missing"] == ["result.txt"]
    # Exactly one remediation turn: 2 turns total.
    assert _event_types(events).count("turn_start") == 2


class _MockSseHandler(BaseHTTPRequestHandler):
    """One-shot OpenAI-compatible SSE responder; records request bodies."""

    request_bodies: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).request_bodies.append(body)
        sse = (
            'data: {"choices":[{"delta":{"content":"done"},'
            '"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n"
        )
        payload = sse.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def test_no_auto_discovery(tmp_path: Path, velites_binary: Path) -> None:
    """EXEC-HARNESS-ISOLATION-001: an AGENTS.md in the cwd is never read —
    the marker string must not appear in the provider request or the event
    stream (context = --system-prompt + --skill + @prompt.md, nothing else).
    """
    marker = "ZERO_AUTO_DISCOVERY_MARKER_7f3d9b"
    (tmp_path / "AGENTS.md").write_text(f"secret instructions: {marker}", encoding="utf-8")
    (tmp_path / "prompt.md").write_text("Say done.", encoding="utf-8")

    _MockSseHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockSseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        proc = _run(
            velites_binary,
            tmp_path,
            [
                "--mode",
                "json",
                "--provider",
                "openai_compat",
                "--model",
                "stub-model",
                # Confinement is out of scope here and fails closed on CI
                # (Linux without bubblewrap); see test_velites_sandbox.py.
                "--no-sandbox",
                "@prompt.md",
            ],
            env_extra={"VELITES_BASE_URL": base_url, "VELITES_API_KEY": "test-key"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert proc.returncode == 0, proc.stderr
    assert _MockSseHandler.request_bodies, "provider received no request"
    for body in _MockSseHandler.request_bodies:
        assert marker not in body, "AGENTS.md content leaked into the provider request"
    assert marker not in proc.stdout, "AGENTS.md content leaked into the event stream"
    events = _events(proc)
    assert events[-1]["type"] == "agent_end"
