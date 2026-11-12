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
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    # #383：host 侧沙箱解析走 shared.code_sandbox.resolve_sandbox_binary
    # （候选 velites-sandbox → velites；自带 data/bin 目录 → PATH）。
    monkeypatch.setattr(
        "server.app.executors._code_sandbox.resolve_sandbox_binary",
        lambda: str(binary),
    )


@contextmanager
def loopback_http_service() -> Iterator[tuple[str, list[str]]]:
    """Serve ``ok`` on an ephemeral host-loopback port and record request paths."""
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()
