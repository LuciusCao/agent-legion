"""CodeExecutor unified runtime contract and reserved execution keys.

Split from tests/executors/test_code_executor.py to stay clear of the
test-file line budget (#207); the execute/sandbox outcome surface stays in
test_code_executor.py and the D12 artifact mirror/restore cases live in
test_code_executor_artifacts.py. The ``context`` fixture and the small
_run/_executor helpers are duplicated per sibling (same shape as the
cancellation suite), matching the convention of the workers suite split.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext
from tests.helpers.velites_sandbox import sandboxed as _sandboxed

REPO_ROOT = Path(__file__).resolve().parents[2]

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


class _FakeJobDb:
    """Minimal JobQueries stand-in for parent-side prefetch tests."""

    def __init__(self, *, runs_error: bool = False) -> None:
        self.path = "postgresql://fake"
        self.jobs_dir = "/fake"
        self._runs_error = runs_error

    def get_run(self, run_id: str) -> dict | None:
        if not run_id:
            return None
        return {"id": run_id, "frozen_pins_json": "{}"}

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
    ctx = replace(context, job={**context.job, "run_id": "b-1"})

    runtime = build_runtime(executor, ctx, CancellationToken())

    assert "_job_db_path" not in runtime
    assert "_jobs_dir" not in runtime
    assert "job_db" not in runtime
    # The SDK-facing batch row is the run row plus the payload synthesized
    # from the run/job freeze columns (RUN-FREEZE-001).
    assert runtime["job_batch"] == {
        "id": "b-1",
        "frozen_pins_json": "{}",
        "source_payload_json": '{"node_config": {}, "task_candidates": []}',
    }
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
        replace(context, node_code=custom_source, job={**context.job, "run_id": "b-1"})
    )

    assert result.status == "completed"
    import json

    data = json.loads((context.job_dir / "out.json").read_text(encoding="utf-8"))
    assert data["batch"] == {
        "id": "b-1",
        "frozen_pins_json": "{}",
        "source_payload_json": '{"node_config": {}, "task_candidates": []}',
    }
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
