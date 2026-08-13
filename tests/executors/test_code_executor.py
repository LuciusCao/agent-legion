from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.config import CodeCapabilityConfig
from server.app.executors.models import ExecutionContext

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_DEBUG_BINARY = REPO_ROOT / "velites" / "target" / "debug" / "velites"


def _velites_binary() -> Path:
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


def _sandbox_backend_available() -> bool:
    """Probe the actual OS sandbox backend, not just the platform."""
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    if sys.platform == "linux":
        return shutil.which("bwrap") is not None
    return False


def _sandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    if not _sandbox_backend_available():
        pytest.skip("no OS sandbox backend (macOS sandbox-exec / Linux bwrap)")
    binary = _velites_binary()
    monkeypatch.setattr(
        "server.app.executors._code_sandbox.shutil.which", lambda _name: str(binary)
    )


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
        workflow_key="question_comprehension_info",
        node_key="fetch_questions",
        capability="fetch_questions",
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "question_comprehension_info",
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


def _write_node(repo_root: Path, name: str, body: str) -> str:
    path = repo_root / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return name


def _executor(
    repo_root: Path,
    path: str,
    *,
    timeout_seconds: int = 60,
) -> CodeExecutor:
    return CodeExecutor(
        "code-default",
        {"fetch_questions": CodeCapabilityConfig(path=path, timeout_seconds=timeout_seconds)},
        repo_root=repo_root,
    )


def test_constructor_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the repository root"):
        _executor(tmp_path, "nodes/missing.py")


def test_constructor_rejects_non_file_path(tmp_path: Path) -> None:
    (tmp_path / "nodes").mkdir()
    with pytest.raises(ValueError, match="inside the repository root"):
        _executor(tmp_path, "nodes")


def test_config_rejects_absolute_and_escape_paths() -> None:
    with pytest.raises(ValueError, match="must not be absolute"):
        CodeCapabilityConfig(path="/etc/passwd")
    with pytest.raises(ValueError, match="must not contain '..'"):
        CodeCapabilityConfig(path="../outside.py")


def test_supports(tmp_path: Path) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    assert executor.supports("fetch_questions")
    assert not executor.supports("other")


def test_execute_missing_capability(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, capability="missing"))
    assert result.status == "failed"
    assert "not supported" in result.error_message


def test_execute_success_writes_expected_outputs(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_ok.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)


def test_execute_fails_when_outputs_missing(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_noop.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "Missing outputs" in result.error_message


def test_execute_propagates_node_exception(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_boom.py",
        """
        def run(job, job_dir, runtime):
            raise RuntimeError("boom")
        """,
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "RuntimeError: boom" in result.error_message


def test_execute_fails_without_run_callable(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_no_run.py", "VALUE = 1\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "callable 'run'" in result.error_message


def test_execute_timeout_kills_child(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_slow.py",
        """
        import time

        def run(job, job_dir, runtime):
            time.sleep(60)
        """,
    )
    executor = _executor(tmp_path, path, timeout_seconds=1)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "timed out after 1s" in result.error_message


def test_cancel_before_start(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.cancel(context.execution_id)
    result = executor.execute(context)
    assert result.status == "cancelled"


def test_execute_custom_node_code_from_source(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """context.node_code (EXEC-CODE-002) runs sandboxed from the string, not the file."""
    _sandboxed(monkeypatch)
    path = _write_node(
        tmp_path,
        "node_builtin.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "builtin"}', encoding="utf-8")
        """,
    )
    custom_source = textwrap.dedent(
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "custom"}', encoding="utf-8")
        """
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, node_code=custom_source))
    assert result.status == "completed"
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == '{"origin": "custom"}'


def test_execute_custom_node_code_without_run_fails(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, node_code="X = 1\n"))
    assert result.status == "failed"
    assert "callable 'run'" in result.error_message


def test_custom_node_code_fails_closed_without_sandbox(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No velites wrapper -> custom code never runs unsandboxed (EXEC-CODE-003)."""
    monkeypatch.setattr("server.app.executors._code_sandbox.shutil.which", lambda _name: None)
    custom_source = textwrap.dedent(
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{}', encoding="utf-8")
        """
    )
    path = _write_node(
        tmp_path,
        "node_builtin.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "builtin"}', encoding="utf-8")
        """,
    )
    executor = _executor(tmp_path, path)

    custom = executor.execute(replace(context, node_code=custom_source))
    assert custom.status == "failed"
    assert "refusing to run unsandboxed" in custom.error_message
    assert not (tmp_path / "out.json").exists()

    # Builtin nodes are unaffected: they keep the bare multiprocessing child.
    builtin = executor.execute(replace(context, node_code=None, execution_id="exec-builtin"))
    assert builtin.status == "completed"
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == '{"origin": "builtin"}'


def test_custom_sandbox_denies_writes_outside_job_dir(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS seatbelt integration: writes outside job_dir/tmp fail with EPERM."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        from pathlib import Path

        def run(job, job_dir, runtime):
            (Path.home() / ".agent-legion-sandbox-probe").write_text("x", encoding="utf-8")
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "failed"
    assert _DENIED_ERROR in result.error_message
    assert not (Path.home() / ".agent-legion-sandbox-probe").exists()


def test_custom_sandbox_denies_reads_outside_allowlist(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo-root secrets (`.env`) and root listing fail with EPERM.

    Only `<repo>/server|workflow_nodes|config|workspace_libs` are
    read-allowed; the root itself is list-only (Python's importer needs the
    listing), never its files. The probe runs inside a throwaway tmp job dir
    and only ever *reads* a repo-tracked path.
    """
    _sandboxed(monkeypatch)
    from server.app.executors._code_sandbox import _SERVER_REPO_ROOT

    env_probe = _SERVER_REPO_ROOT / ".env"
    if not env_probe.exists():
        pytest.skip("worktree has no .env to probe")
    custom_source = textwrap.dedent(
        f"""
        import os
        from pathlib import Path

        def run(job, job_dir, runtime):
            Path({str(env_probe)!r}).read_text(encoding="utf-8")
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "failed"
    assert _DENIED_ERROR in result.error_message


def test_custom_sandbox_env_is_whitelisted(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No AGENT_LEGION_* / CMS_* / BASECMS_* variables reach the child."""
    _sandboxed(monkeypatch)
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN", "should-not-leak")
    monkeypatch.setenv("CMS_TOKEN", "should-not-leak")
    custom_source = textwrap.dedent(
        """
        import json
        import os

        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "completed"
    import json

    keys = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    leaked = [key for key in keys if key.startswith(("AGENT_LEGION_", "CMS_", "BASECMS_"))]
    assert leaked == []
    assert "PATH" in keys  # sanity: the whitelist did apply, env not empty


def test_custom_sandbox_denies_network_by_default(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outbound network is denied unless the capability opts in (EXEC-CODE-003)."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        import urllib.request

        def run(job, job_dir, runtime):
            urllib.request.urlopen("http://127.0.0.1:9/", timeout=2)
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")

    denied = _executor(tmp_path, path).execute(replace(context, node_code=custom_source))
    assert denied.status == "failed"
    assert _NET_ERROR in denied.error_message

    executor_with_net = CodeExecutor(
        "code-default",
        {
            "fetch_questions": CodeCapabilityConfig(
                path=path, timeout_seconds=60, sandbox_network=True
            )
        },
        repo_root=tmp_path,
    )
    allowed = executor_with_net.execute(
        replace(context, node_code=custom_source, execution_id="exec-net")
    )
    assert allowed.status == "failed"
    # With network allowed the failure is a plain connection refusal, not EPERM.
    assert "Operation not permitted" not in allowed.error_message


def test_repo_read_subdirs_include_workspace_libs() -> None:
    """Custom forks of the CMS-calling builtin nodes import workspace_libs
    inside the sandbox (regression: the CMS client moved server/app/cms →
    workspace_libs/cms and the deny-default sandbox lost read access)."""
    from server.app.executors._code_sandbox import _REPO_READ_SUBDIRS

    assert "workspace_libs" in _REPO_READ_SUBDIRS


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
    assert _read_result(target, log) is not None  # pickle is rejected, not executed

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
    custom_source = (
        "import subprocess\n"
        "import time\n\n"
        "def run(job, job_dir, runtime):\n"
        "    marker = job_dir / 'grandchild-survived'\n"
        "    subprocess.Popen(['/bin/sh', '-c', f'sleep 2; touch {marker}'])\n"
        "    time.sleep(30)\n"
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)

    import threading as _threading

    from server.app.executors.cancellation import CancellationToken

    token = CancellationToken(_threading.Event())
    canceller = _threading.Timer(0.5, token.cancel)
    canceller.start()
    sandboxed = replace(
        context,
        node_code=custom_source,
        runtime={"cancellation": token},
    )
    result = executor.execute(sandboxed)
    canceller.cancel()

    assert result.status == "cancelled"
    time.sleep(2.5)
    assert not (tmp_path / "grandchild-survived").exists()


# ---------------------------------------------------------------------------
# Unified runtime contract (node-sdk-and-worker-execution design §3/§5)


class _FakeJobDb:
    """Minimal JobQueries stand-in for parent-side prefetch tests."""

    def __init__(self, *, runs_error: bool = False) -> None:
        self.path = "postgresql://fake"
        self.jobs_dir = "/fake"
        self._runs_error = runs_error

    def get_batch(self, batch_id: str) -> dict | None:
        if not batch_id:
            return None
        return {"id": batch_id, "source_payload_json": "{}"}

    def list_node_runs(self, job_id: str) -> list[dict]:
        if self._runs_error:
            raise RuntimeError("db down")
        return [
            {"node_key": "fetch_questions", "skill_version": "abc123"},
            {"node_key": "review", "skill_version": None},
        ]


def test_build_runtime_prefetches_inputs_and_hides_db(
    tmp_path: Path, context: ExecutionContext
) -> None:
    """Builtin and sandboxed children share one runtime: DB-derived inputs are
    prefetched by the parent; no ``job_db`` handle or DSN leaks into it."""
    from server.app.executors._code_runtime import build_runtime
    from server.app.executors.cancellation import CancellationToken

    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.job_db = _FakeJobDb()
    ctx = replace(context, job={**context.job, "batch_id": "b-1"})

    runtime = build_runtime(executor, ctx, CancellationToken())

    assert "_job_db_path" not in runtime
    assert "_jobs_dir" not in runtime
    assert "job_db" not in runtime
    assert runtime["job_batch"] == {"id": "b-1", "source_payload_json": "{}"}
    assert runtime["skill_versions"] == {"fetch_questions": "abc123"}


def test_build_runtime_skill_versions_degrade_on_db_error(
    tmp_path: Path, context: ExecutionContext
) -> None:
    from server.app.executors._code_runtime import build_runtime
    from server.app.executors.cancellation import CancellationToken

    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.job_db = _FakeJobDb(runs_error=True)

    runtime = build_runtime(executor, context, CancellationToken())

    assert runtime["skill_versions"] == {}


def _capture_token_service(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    from types import SimpleNamespace

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "server.app.executors._code_runtime.ConnectionTokenService",
        lambda dsn: SimpleNamespace(report_auth_failure=lambda key: calls.append((dsn, key))),
    )
    return calls


def test_consume_auth_failure_marker_invalidates_cached_token(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The node records the fact; the parent performs the privileged
    invalidation and removes the marker (design §5.3)."""
    from types import SimpleNamespace

    from server.app.executors._code_runtime import consume_auth_failure_marker
    from workspace_libs.node_sdk import AUTH_FAILURE_MARKER_PATH

    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)
    marker = context.job_dir / AUTH_FAILURE_MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("cms-internal", encoding="utf-8")

    consume_auth_failure_marker(executor, context)

    assert calls == [("postgresql://fake", "cms-internal")]
    assert not marker.exists()


def test_consume_auth_failure_marker_falls_back_to_node_config_connection(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from server.app.executors._code_runtime import consume_auth_failure_marker
    from workspace_libs.node_sdk import AUTH_FAILURE_MARKER_PATH

    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)
    marker = context.job_dir / AUTH_FAILURE_MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    ctx = replace(context, node_config={"connection": "cms-fallback"})

    consume_auth_failure_marker(executor, ctx)

    assert calls == [("postgresql://fake", "cms-fallback")]


def test_consume_auth_failure_marker_noop_without_marker(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from server.app.executors._code_runtime import consume_auth_failure_marker

    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)

    consume_auth_failure_marker(executor, context)

    assert calls == []


def test_builtin_child_auth_failure_marker_reaches_parent(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a builtin node reporting via the SDK marker triggers the
    parent's token invalidation after the child exits."""
    from types import SimpleNamespace

    path = _write_node(
        tmp_path,
        "node_auth.py",
        """
        from workspace_libs.node_sdk import NodeContext

        def run(job, job_dir, runtime):
            NodeContext(job, job_dir, runtime).report_auth_failure()
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
    )
    executor = _executor(tmp_path, path)
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)
    ctx = replace(context, node_config={"connection": "cms-internal"})

    result = executor.execute(ctx)

    assert result.status == "completed"
    assert calls == [("postgresql://fake", "cms-internal")]


def test_sandboxed_custom_node_can_use_node_sdk(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distribution contract: the SDK is importable inside the velites sandbox
    and prefetched inputs (batch, skill versions) reach custom nodes."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        import json

        from workspace_libs.node_sdk import NodeContext

        def run(job, job_dir, runtime):
            ctx = NodeContext(job, job_dir, runtime)
            payload = {"batch": ctx.batch, "skill_versions": ctx.skill_versions}
            (job_dir / "out.json").write_text(json.dumps(payload), encoding="utf-8")
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.job_db = _FakeJobDb()

    result = executor.execute(
        replace(context, node_code=custom_source, job={**context.job, "batch_id": "b-1"})
    )

    assert result.status == "completed"
    import json

    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["batch"] == {"id": "b-1", "source_payload_json": "{}"}
    assert data["skill_versions"] == {"fetch_questions": "abc123"}


def test_pathless_capability_requires_custom_code(
    tmp_path: Path, context: ExecutionContext
) -> None:
    """Pathless (custom-code-only) capability: supported, but nothing to run
    without custom node code — a clear config error, not "not supported"."""
    executor = CodeExecutor(
        "code-default",
        {"fetch_questions": CodeCapabilityConfig()},
        repo_root=tmp_path,
    )
    assert executor.supports("fetch_questions")

    result = executor.execute(context)

    assert result.status == "failed"
    assert "no builtin code path" in result.error_message


def test_pathless_capability_runs_custom_code_sandboxed(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pathless capability executes its custom code in the velites sandbox
    (EXEC-CODE-003), exactly like a builtin-path capability with custom code."""
    _sandboxed(monkeypatch)
    executor = CodeExecutor(
        "code-default",
        {"fetch_questions": CodeCapabilityConfig()},
        repo_root=tmp_path,
    )
    custom_source = textwrap.dedent(
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "custom"}', encoding="utf-8")
        """
    )

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "completed"
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == '{"origin": "custom"}'
