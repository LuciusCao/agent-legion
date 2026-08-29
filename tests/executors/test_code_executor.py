"""CodeExecutor execute/sandbox outcome surface: dispatch fallbacks, output
validation, fail-closed sandboxing and cancellation.

Split from the pre-#207 single-file suite: the unified runtime contract and
reserved execution keys now live in test_code_executor_runtime.py, and the
D12 artifact mirror/restore cases in test_code_executor_artifacts.py.
"""

from __future__ import annotations

import sys
import textwrap
import time
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.contracts import CodeCapabilityConfig
from server.app.executors.models import ExecutionContext
from tests.helpers import pid_is_running
from tests.helpers.velites_sandbox import sandboxed as _sandboxed

REPO_ROOT = Path(__file__).resolve().parents[2]


# Rejection semantics differ per backend: seatbelt denies with EPERM, while
# bwrap's selective binds leave unmounted paths simply absent (ENOENT); an
# isolated netns has no route (ENETUNREACH).
_DENIED_ERROR = (
    "Operation not permitted" if sys.platform == "darwin" else "No such file or directory"
)
_NET_ERROR = "Operation not permitted" if sys.platform == "darwin" else "Network is unreachable"


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="code-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="demo_workflow",
        node_key="fetch_items",
        capability="fetch_items",
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "demo_workflow",
            "source_type": "question",
            "source_id": "q-1",
            "batch_id": "",
            "title": "Question 1",
            "storage_dir": str(tmp_path),
            "stem": "",
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("out.json",),
    )


def _source(body: str) -> str:
    """Node code text (DB-published since #96: all code executes from text)."""
    return textwrap.dedent(body)


def _executor() -> CodeExecutor:
    return CodeExecutor(repo_root=REPO_ROOT)


def _run(executor: CodeExecutor, context: ExecutionContext, source: str, **over):
    return executor.execute(replace(context, node_code=_source(source), **over))


def test_config_rejects_retired_path_key() -> None:
    """The capability ``path`` binding is retired (#96): extra=forbid rejects it."""
    with pytest.raises(ValueError):
        CodeCapabilityConfig(path="workflow_nodes/example_intake.py")


def test_supports() -> None:
    # Single implicit code pool (P-0.5): the adapter accepts any capability;
    # dispatch fails nodes without published node code earlier (EXEC-CODE-002).
    executor = _executor()
    assert executor.supports("fetch_items")
    assert executor.supports("other")


def test_execute_unknown_capability_runs_with_platform_defaults(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No capability declaration is needed (P-0.5): the run falls back to the
    platform default timeout/network and executes the published code text."""
    _sandboxed(monkeypatch)
    result = _run(
        _executor(),
        context,
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
        capability="missing",
    )
    assert result.status == "completed"


def test_execute_without_node_code_is_a_config_error(context: ExecutionContext) -> None:
    """Dispatch resolves code text and fails earlier; this is the backstop."""
    executor = _executor()
    result = executor.execute(context)
    assert result.status == "failed"
    assert "no published node code" in result.error_message


def test_execute_success_writes_expected_outputs(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    result = _run(
        _executor(),
        context,
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
    )
    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)


def test_execute_fails_when_outputs_missing(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    result = _run(_executor(), context, "def run(job, job_dir, runtime):\n    pass\n")
    assert result.status == "failed"
    assert "Missing outputs" in result.error_message


def test_execute_propagates_node_exception(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    result = _run(
        _executor(),
        context,
        """
        def run(job, job_dir, runtime):
            raise RuntimeError("boom")
        """,
    )
    assert result.status == "failed"
    assert "RuntimeError: boom" in result.error_message


def test_execute_fails_without_run_callable(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    result = _run(_executor(), context, "VALUE = 1\n")
    assert result.status == "failed"
    assert "callable 'run'" in result.error_message


def test_execute_timeout_kills_child(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    result = _run(
        _executor(),
        context,
        """
        import time

        def run(job, job_dir, runtime):
            time.sleep(60)
        """,
        node_config={"timeout_seconds": 1},
    )
    assert result.status == "failed"
    assert "timed out after 1s" in result.error_message


def test_cancel_before_start(context: ExecutionContext) -> None:
    executor = _executor()
    executor.cancel(context.execution_id)
    result = executor.execute(context)
    assert result.status == "cancelled"


def test_node_code_fails_closed_without_sandbox(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No velites wrapper -> node code never runs unsandboxed (EXEC-CODE-003).

    Since #96 this applies to ALL code nodes: the bare multiprocessing child
    for repo-file builtins is gone with the path mechanism.
    """
    monkeypatch.setattr("server.app.executors._code_sandbox.shutil.which", lambda _name: None)
    result = _run(
        _executor(),
        context,
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{}', encoding="utf-8")
        """,
    )
    assert result.status == "failed"
    assert "refusing to run unsandboxed" in result.error_message
    assert not (context.job_dir / "out.json").exists()


def test_custom_sandbox_denies_writes_outside_job_dir(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS seatbelt integration: writes outside job_dir/tmp fail with EPERM."""
    _sandboxed(monkeypatch)
    result = _run(
        _executor(),
        context,
        """
        from pathlib import Path

        def run(job, job_dir, runtime):
            (Path.home() / ".agent-legion-sandbox-probe").write_text("x", encoding="utf-8")
        """,
    )

    assert result.status == "failed"
    assert _DENIED_ERROR in result.error_message
    assert not (Path.home() / ".agent-legion-sandbox-probe").exists()


def test_custom_sandbox_denies_reads_outside_allowlist(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo-root secrets (`.env`) and root listing fail with EPERM.

    Only `<repo>/server|workflow_nodes|config|workspace_libs|examples` are
    read-allowed; the root itself is list-only (Python's importer needs the
    listing), never its files. The probe runs inside a throwaway tmp job dir
    and only ever *reads* a repo-tracked path.
    """
    _sandboxed(monkeypatch)
    from server.app.executors._code_sandbox import _SERVER_REPO_ROOT

    env_probe = _SERVER_REPO_ROOT / ".env"
    if not env_probe.exists():
        pytest.skip("worktree has no .env to probe")
    result = _run(
        _executor(),
        context,
        f"""
        from pathlib import Path

        def run(job, job_dir, runtime):
            Path({str(env_probe)!r}).read_text(encoding="utf-8")
        """,
    )

    assert result.status == "failed"
    assert _DENIED_ERROR in result.error_message


def test_custom_sandbox_env_is_whitelisted(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No AGENT_LEGION_* / CMS_* / BASECMS_* variables reach the child."""
    _sandboxed(monkeypatch)
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN", "should-not-leak")
    monkeypatch.setenv("CMS_TOKEN", "should-not-leak")
    result = _run(
        _executor(),
        context,
        """
        import json
        import os

        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
        """,
    )

    assert result.status == "completed"
    import json

    keys = json.loads((context.job_dir / "out.json").read_text(encoding="utf-8"))
    leaked = [key for key in keys if key.startswith(("AGENT_LEGION_", "CMS_", "BASECMS_"))]
    assert leaked == []
    assert "PATH" in keys  # sanity: the whitelist did apply, env not empty


def test_custom_sandbox_denies_network_by_default(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outbound network is denied unless the node opts in (EXEC-CODE-003, P-0.5)."""
    _sandboxed(monkeypatch)
    source = _source(
        """
        import urllib.request

        def run(job, job_dir, runtime):
            urllib.request.urlopen("http://127.0.0.1:9/", timeout=2)
        """
    )

    denied = _executor().execute(replace(context, node_code=source))
    assert denied.status == "failed"
    assert _NET_ERROR in denied.error_message

    allowed = _executor().execute(
        replace(
            context,
            node_code=source,
            execution_id="exec-net",
            node_config={"sandbox_network": True},
        )
    )
    assert allowed.status == "failed"
    # With network allowed the failure is a plain connection refusal, not EPERM.
    assert "Operation not permitted" not in allowed.error_message


def test_repo_read_subdirs_include_sdk_but_not_examples() -> None:
    """Node code imports workspace_libs (the node SDK) inside the sandbox, so
    the deny-default sandbox must keep read access to it; examples/ is no
    longer read-allowed — the demo intake node consumes its knowledge
    markdown as a material via the static cache root (design §9)."""
    from server.app.executors._code_sandbox import _REPO_READ_SUBDIRS

    assert "workspace_libs" in _REPO_READ_SUBDIRS
    assert "examples" not in _REPO_READ_SUBDIRS


def test_read_result_validates_json_schema(tmp_path: Path) -> None:
    """The result file is JSON with a strict schema — never pickle (EXEC-CODE-003)."""
    from server.app.executors._code_sandbox import _read_result

    target = tmp_path / "result.json"
    log = tmp_path / "run.log"

    target.write_text('{"status": "ok", "message": null}', encoding="utf-8")
    assert _read_result(target, log) is None

    target.write_text('{"status": "error", "message": "boom"}', encoding="utf-8")
    failure = _read_result(target, log)
    assert failure is not None and failure.status == "failed"
    assert failure.error_message == "boom"

    import pickle

    target.write_bytes(pickle.dumps(("ok", None)))
    assert _read_result(target, log) is not None

    for bad in (
        '{"status": "ok"}',
        '["ok", null]',
        '{"status": "yep", "message": null}',
        "not json",
    ):
        target.write_text(bad, encoding="utf-8")
        failure = _read_result(target, log)
        assert failure is not None and failure.status == "failed", bad


def test_custom_cancel_kills_whole_process_group(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation kills the exec'd child's whole group (no orphaned grandchildren)."""
    _sandboxed(monkeypatch)
    marker = tmp_path / "grandchild-survived"
    pid_file = tmp_path / "grandchild.pid"
    # The grandchild outlives the sandboxed child by design: it sleeps, then
    # touches the marker. The test asserts cancellation killed the whole
    # group BEFORE that touch. Watching the grandchild's PID exit (instead of
    # sleeping past its touch deadline) keeps the assertion race-free under
    # load: once the PID is gone, a surviving shell can no longer touch.
    custom_source = (
        "import subprocess\n"
        "import time\n\n"
        "def run(job, job_dir, runtime):\n"
        f"    marker = job_dir / '{marker.name}'\n"
        "    proc = subprocess.Popen(['/bin/sh', '-c', f'sleep 2; touch {marker}'])\n"
        f"    (job_dir / '{pid_file.name}').write_text(str(proc.pid))\n"
        "    time.sleep(30)\n"
    )

    import threading as _threading

    from server.app.executors.cancellation import CancellationToken

    token = CancellationToken(_threading.Event())
    sandboxed = replace(
        context,
        node_code=custom_source,
        runtime={"cancellation": token},
    )

    # Cancel deterministically mid-execution: run the executor on a thread and
    # fire the token only after the pid file proves the grandchild spawned.
    # A wall-clock timer raced the child's startup under load (the same race
    # CI just caught in the orphan-reaper twin of this test).
    outcome_holder: dict = {}

    def run() -> None:
        try:
            outcome_holder["result"] = _executor().execute(sandboxed)
        except Exception as exc:
            # Re-raised below; a bare holder["result"] KeyError would bury it.
            outcome_holder["error"] = exc

    thread = _threading.Thread(target=run)
    thread.start()
    spawn_deadline = time.monotonic() + 10.0
    try:
        while not pid_file.exists():
            assert time.monotonic() < spawn_deadline, "grandchild never started"
            time.sleep(0.05)
        token.cancel()
    finally:
        thread.join(timeout=10.0)
    assert not thread.is_alive()
    if "error" in outcome_holder:
        raise outcome_holder["error"]

    result = outcome_holder["result"]
    assert result.status == "cancelled"
    pid = int(pid_file.read_text().strip())
    # The grandchild must be dead well before its 2s sleep ends. Fresh
    # deadline: the spawn phase above may have consumed most of its own.
    death_deadline = time.monotonic() + 10.0
    while pid_is_running(pid):
        assert time.monotonic() < death_deadline, (
            f"grandchild {pid} survived cancellation; marker={marker}"
        )
        time.sleep(0.05)
    assert not marker.exists()
