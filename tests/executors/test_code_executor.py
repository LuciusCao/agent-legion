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
from server.app.executors.contracts import CodeCapabilityConfig
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


def test_repo_read_subdirs_include_sdk_and_examples() -> None:
    """Node code imports workspace_libs (the node SDK) inside the sandbox, so
    the deny-default sandbox must keep read access to it; examples/ is
    read-allowed for the demo intake node's knowledge markdown (#96)."""
    from server.app.executors._code_sandbox import _REPO_READ_SUBDIRS

    assert "workspace_libs" in _REPO_READ_SUBDIRS
    assert "examples" in _REPO_READ_SUBDIRS


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
    result = _executor().execute(sandboxed)
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
            {"node_key": "fetch_items", "skill_version": "abc123"},
            {"node_key": "review", "skill_version": None},
        ]


def test_build_runtime_prefetches_inputs_and_hides_db(
    tmp_path: Path, context: ExecutionContext
) -> None:
    """Every code child shares one runtime: DB-derived inputs are prefetched
    by the parent; no ``job_db`` handle or DSN leaks into it. The host root
    rides as ``root_dir`` (nodes never use ``__file__``)."""
    from server.app.executors._code_runtime import build_runtime
    from server.app.executors.cancellation import CancellationToken

    executor = _executor()
    executor.job_db = _FakeJobDb()
    ctx = replace(context, job={**context.job, "batch_id": "b-1"})

    runtime = build_runtime(executor, ctx, CancellationToken())

    assert "_job_db_path" not in runtime
    assert "_jobs_dir" not in runtime
    assert "job_db" not in runtime
    assert runtime["job_batch"] == {"id": "b-1", "source_payload_json": "{}"}
    assert runtime["skill_versions"] == {"fetch_items": "abc123"}
    assert runtime["root_dir"] == str(REPO_ROOT)


def test_build_runtime_whitelists_settings_config_sections(
    context: ExecutionContext,
) -> None:
    """VAULT-SECRET-001: the sandboxed child is user code, so instance-level
    settings sections (vault/auth/database/...) never cross into it."""
    from server.app.executors._code_runtime import build_runtime
    from server.app.executors.cancellation import CancellationToken

    executor = _executor()
    executor.settings_config = {
        "vault": {"master_key": "fernet-key-material"},
        "database": {"url": "postgresql://user:db-pw@db/agent_legion"},
    }

    runtime = build_runtime(executor, context, CancellationToken())

    assert runtime["settings_config"] == {}


def test_build_runtime_skill_versions_degrade_on_db_error(
    context: ExecutionContext,
) -> None:
    from server.app.executors._code_runtime import build_runtime
    from server.app.executors.cancellation import CancellationToken

    executor = _executor()
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
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The node records the fact; the parent performs the privileged
    invalidation and removes the marker (design §5.3)."""
    from types import SimpleNamespace

    from server.app.executors._code_runtime import consume_auth_failure_marker
    from workspace_libs.node_sdk import AUTH_FAILURE_MARKER_PATH

    executor = _executor()
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)
    marker = context.job_dir / AUTH_FAILURE_MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("cms-internal", encoding="utf-8")

    consume_auth_failure_marker(executor, context)

    assert calls == [("postgresql://fake", "cms-internal")]
    assert not marker.exists()


def test_consume_auth_failure_marker_falls_back_to_node_config_connection(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from server.app.executors._code_runtime import consume_auth_failure_marker
    from workspace_libs.node_sdk import AUTH_FAILURE_MARKER_PATH

    executor = _executor()
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)
    marker = context.job_dir / AUTH_FAILURE_MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    ctx = replace(context, node_config={"connection": "cms-fallback"})

    consume_auth_failure_marker(executor, ctx)

    assert calls == [("postgresql://fake", "cms-fallback")]


def test_consume_auth_failure_marker_noop_without_marker(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from server.app.executors._code_runtime import consume_auth_failure_marker

    executor = _executor()
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)

    consume_auth_failure_marker(executor, context)

    assert calls == []


def test_sandboxed_child_auth_failure_marker_reaches_parent(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a node reporting via the SDK marker triggers the parent's
    token invalidation after the sandboxed child exits."""
    from types import SimpleNamespace

    _sandboxed(monkeypatch)
    executor = _executor()
    executor.job_db = SimpleNamespace(path="postgresql://fake")
    calls = _capture_token_service(monkeypatch)
    ctx = replace(context, node_config={"connection": "cms-internal"})

    result = _run(
        executor,
        ctx,
        """
        from workspace_libs.node_sdk import NodeContext

        def run(job, job_dir, runtime):
            NodeContext(job, job_dir, runtime).report_auth_failure()
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
    )

    assert result.status == "completed"
    assert calls == [("postgresql://fake", "cms-internal")]


def test_sandboxed_custom_node_can_use_node_sdk(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
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
    executor = _executor()
    executor.job_db = _FakeJobDb()

    result = executor.execute(
        replace(context, node_code=custom_source, job={**context.job, "batch_id": "b-1"})
    )

    assert result.status == "completed"
    import json

    data = json.loads((context.job_dir / "out.json").read_text(encoding="utf-8"))
    assert data["batch"] == {"id": "b-1", "source_payload_json": "{}"}
    assert data["skill_versions"] == {"fetch_items": "abc123"}


def test_sandboxed_node_can_use_framework_modules(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distribution contract (#82): the framework-layer modules (http_client,
    media) and the @entrypoint decorator are importable inside the sandbox."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        from workspace_libs.http_client import HttpServiceError, bearer_headers
        from workspace_libs.media import parse_srt
        from workspace_libs.node_sdk import NodeContext, entrypoint

        @entrypoint
        def run(ctx):
            headers = bearer_headers("tok")
            subs = parse_srt("1\\n00:00:01,000 --> 00:00:02,000\\n你好\\n\\n")
            ctx.artifacts.write_json(
                "out.json", {"auth": headers["Authorization"], "subs": len(subs)}
            )
        """
    )
    executor = _executor()

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "completed"
    import json

    data = json.loads((context.job_dir / "out.json").read_text(encoding="utf-8"))
    assert data == {"auth": "Bearer tok", "subs": 1}


# ---------------------------------------------------------------------------
# P-0.5: timeout/sandbox_network come from the resolved node config
# (reserved execution keys); the platform defaults (600s / deny) are the only
# fallback — the executor capability layer is retired (step 3).


def test_execute_timeout_comes_from_node_config(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatch-resolved node config carries the timeout; missing or
    invalid reserved keys fall back to the platform default (600s)."""
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


def test_sandbox_network_opt_in_comes_from_node_config(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sandbox_network=true in the resolved node config opts the node into
    network access; anything else keeps the sandbox default (deny)."""
    _sandboxed(monkeypatch)
    source = _source(
        """
        import urllib.request

        def run(job, job_dir, runtime):
            urllib.request.urlopen("http://127.0.0.1:9/", timeout=2)
        """
    )

    denied = _executor().execute(
        replace(context, node_code=source, node_config={"sandbox_network": False})
    )
    assert denied.status == "failed"
    assert _NET_ERROR in denied.error_message

    # With network allowed the failure is a plain connection refusal, not EPERM.
    allowed = _executor().execute(
        replace(
            context,
            node_code=source,
            execution_id="exec-net-node",
            node_config={"sandbox_network": True},
        )
    )
    assert allowed.status == "failed"
    assert "Operation not permitted" not in allowed.error_message
