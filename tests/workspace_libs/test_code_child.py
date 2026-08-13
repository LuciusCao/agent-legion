"""Contract tests for workspace_libs/code_child.py (sandboxed child entry).

The child speaks the same protocol as the legacy
``server.app.executors._code_child``: a parent-produced pickle payload on
stdin (code/job/job_dir/runtime — resolved secrets never touch the sandboxed
filesystem), a strictly-JSON result file, and SIGTERM → cooperative cancel.
These tests spawn the real module entrypoint without the velites wrapper;
the sandboxed integration path is covered by tests/executors/.
"""

from __future__ import annotations

import ast
import json
import os
import pickle
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_db

_EXECUTION_PLANE_MODULES = (
    "workspace_libs/cancellation.py",
    "workspace_libs/code_loader.py",
    "workspace_libs/code_child.py",
)


def _spawn_child(result_path: Path, payload: dict) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{python_path}" if python_path else str(REPO_ROOT)
    return subprocess.Popen(
        [sys.executable, "-m", "workspace_libs.code_child", str(result_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=result_path.parent,
        env=env,
    )


def _run_child(result_path: Path, payload: dict) -> subprocess.CompletedProcess[bytes]:
    proc = _spawn_child(result_path, payload)
    stdout, _ = proc.communicate(pickle.dumps(payload), timeout=30)
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout)


def _payload(code: str, job_dir: Path) -> dict:
    return {
        "code": textwrap.dedent(code),
        "job": {"id": "job-1"},
        "job_dir": str(job_dir),
        "runtime": {"node_key": "node_a", "capability": "cap_a"},
    }


def _read_result(result_path: Path) -> dict:
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_child_ok_writes_json_result(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    payload = _payload(
        """
        def run(job, job_dir, runtime):
            token = runtime["cancellation"]
            (job_dir / "out.json").write_text(
                '{"token": %s}' % ("true" if token.is_cancelled() is False else "false")
            )
        """,
        tmp_path,
    )
    completed = _run_child(result_path, payload)
    assert completed.returncode == 0, completed.stdout
    assert _read_result(result_path) == {"status": "ok", "message": None}
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == {"token": True}


def test_child_error_is_reported_as_json_not_pickle(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    payload = _payload(
        """
        def run(job, job_dir, runtime):
            raise RuntimeError("boom")
        """,
        tmp_path,
    )
    completed = _run_child(result_path, payload)
    assert completed.returncode == 1
    assert _read_result(result_path) == {"status": "error", "message": "RuntimeError: boom"}


def test_child_missing_run_reports_error(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    completed = _run_child(result_path, _payload("x = 1\n", tmp_path))
    assert completed.returncode == 1
    result = _read_result(result_path)
    assert result["status"] == "error"
    assert "does not expose a callable 'run'" in result["message"]


def test_child_sigterm_cancels_token_and_reports(tmp_path: Path) -> None:
    """SIGTERM cancels the runtime token first, then unwinds via SystemExit."""
    result_path = tmp_path / "result.json"
    payload = _payload(
        """
        import time

        def run(job, job_dir, runtime):
            (job_dir / "started").write_text("1")
            try:
                while True:
                    time.sleep(0.01)
            except BaseException:
                if runtime["cancellation"].is_cancelled():
                    (job_dir / "token-cancelled").write_text("1")
                raise
        """,
        tmp_path,
    )
    proc = _spawn_child(result_path, payload)
    try:
        proc.stdin.write(pickle.dumps(payload))
        proc.stdin.close()
        deadline = time.monotonic() + 10
        while not (tmp_path / "started").exists():
            assert time.monotonic() < deadline, "child did not start running the node"
            assert proc.poll() is None, f"child exited early: {proc.stdout.read()!r}"
            time.sleep(0.02)
        proc.send_signal(signal.SIGTERM)
        _, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 1
    result = _read_result(result_path)
    assert result["status"] == "error"
    assert result["message"] == "SystemExit: 130"
    # The SIGTERM handler cancelled the token before SystemExit unwound the run.
    assert (tmp_path / "token-cancelled").exists()


def _workspace_libs_closure(path: Path) -> set[str]:
    """Import roots in the workspace_libs-local closure of a module file."""
    roots: set[str] = set()
    visited: set[Path] = set()
    stack = [path]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        tree = ast.parse(current.read_text(encoding="utf-8"), filename=str(current))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
                continue
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                module = node.module
                roots.add(module.split(".")[0])
            if module and module.startswith("workspace_libs."):
                candidate = REPO_ROOT.joinpath(*module.split(".")).with_suffix(".py")
                if candidate.is_file():
                    stack.append(candidate)
    return roots


@pytest.mark.parametrize("module_path", _EXECUTION_PLANE_MODULES)
def test_execution_plane_modules_never_import_server(module_path: str) -> None:
    """The sandbox child runs without a repo checkout: workspace_libs +
    stdlib only, zero server.app imports in the whole local closure."""
    roots = _workspace_libs_closure(REPO_ROOT / module_path)
    assert "server" not in roots, f"{module_path} pulls server.* into its closure"
