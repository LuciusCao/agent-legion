"""CodeExecutor artifact mirror/restore (D12, EXEC-ARTIFACT-STORE-001).

Split from tests/executors/test_code_executor.py to stay clear of the
test-file line budget (#207); the execute/sandbox outcome surface stays in
test_code_executor.py and the unified runtime contract lives in
test_code_executor_runtime.py. The ``context`` fixture and the small
_run/_executor helpers are duplicated per sibling (same shape as the
cancellation suite), matching the convention of the workers suite split.
"""

from __future__ import annotations

import hashlib
import io
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.artifact_restore import restore_missing_inputs
from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext
from tests.helpers.velites_sandbox import sandboxed as _sandboxed

REPO_ROOT = Path(__file__).resolve().parents[2]


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


class _FakeArtifactStore:
    """In-memory JobArtifactObjectStore stand-in (lookup/open_stream/upload)."""

    def __init__(
        self,
        rows: dict[str, dict] | None = None,
        objects: dict[str, bytes] | None = None,
        upload_error: Exception | None = None,
    ) -> None:
        self._rows = rows or {}
        self._objects = objects or {}
        self._upload_error = upload_error
        self.uploaded: list[str] = []

    def lookup(self, job_id: str, name: str) -> dict | None:
        return self._rows.get(name)

    def open_stream(self, row: dict) -> io.BytesIO:
        return io.BytesIO(self._objects[str(row["storage_key"])])

    def upload(self, *, name: str, **_: object) -> None:
        if self._upload_error is not None:
            raise self._upload_error
        self.uploaded.append(name)


def _stored_input(data: bytes, *, content_hash: str | None = None) -> _FakeArtifactStore:
    key = "jobs/ws-a/job-1/upstream.json"
    row = {
        "storage_key": key,
        "content_hash": (
            content_hash if content_hash is not None else hashlib.sha256(data).hexdigest()
        ),
    }
    return _FakeArtifactStore(rows={"upstream.json": row}, objects={key: data})


def test_restore_missing_inputs_rematerializes_evicted_input(tmp_path: Path) -> None:
    """A targeted rerun with an evicted upstream artifact gets it streamed
    back from object storage (.part temp + os.replace) before the node runs."""
    payload = b'{"items": [1, 2]}'

    restore_missing_inputs(
        _stored_input(payload), job_id="job-1", job_dir=tmp_path, inputs=("upstream.json",)
    )

    assert (tmp_path / "upstream.json").read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))


def test_restore_missing_inputs_rejects_unsafe_names(tmp_path: Path) -> None:
    """Path-traversal names never reach lookup/open_stream."""
    store = _stored_input(b"x")

    restore_missing_inputs(
        store, job_id="job-1", job_dir=tmp_path, inputs=("../evil", "sub/dir.json")
    )

    assert list(tmp_path.iterdir()) == []


def test_restore_missing_inputs_drops_hash_mismatch(tmp_path: Path) -> None:
    """A restored file failing the manifest content-hash check is deleted:
    the node errors on the missing input instead of reading corrupt bytes."""
    restore_missing_inputs(
        _stored_input(b"tampered", content_hash="0" * 64),
        job_id="job-1",
        job_dir=tmp_path,
        inputs=("upstream.json",),
    )

    assert not (tmp_path / "upstream.json").exists()
    assert not list(tmp_path.glob("*.part"))


def test_restore_missing_inputs_keeps_node_semantics_on_storage_error(
    tmp_path: Path,
) -> None:
    """Storage failures are swallowed per file: the input stays missing and
    the node errors on it naturally — restore never flips node semantics."""

    class _DownStore:
        def lookup(self, job_id: str, name: str) -> dict | None:
            raise RuntimeError("storage down")

    restore_missing_inputs(_DownStore(), job_id="job-1", job_dir=tmp_path, inputs=("in.json",))
    restore_missing_inputs(None, job_id="job-1", job_dir=tmp_path, inputs=("in.json",))

    assert not (tmp_path / "in.json").exists()


def test_check_outputs_survives_artifact_store_misconfiguration(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken storage configuration (e.g. a missing secret file raising
    inside build_s3_settings) disables mirroring instead of failing the node."""

    def _raise() -> None:
        raise FileNotFoundError("secret file missing")

    monkeypatch.setattr("server.app.executors.code.build_s3_storage", _raise)
    (context.job_dir / "out.json").write_text("{}", encoding="utf-8")

    result = _executor()._check_outputs(context)

    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)


def test_failed_storage_probe_is_cached(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistently broken storage config probes once: the failure is cached
    instead of re-running (and re-logging) the failing build on every node."""
    calls = 0

    def _raise() -> None:
        nonlocal calls
        calls += 1
        raise FileNotFoundError("secret file missing")

    monkeypatch.setattr("server.app.executors.code.build_s3_storage", _raise)
    executor = _executor()

    with pytest.raises(FileNotFoundError):
        executor._object_store()
    assert executor._object_store() is None
    assert calls == 1


def test_upload_failure_keeps_node_completed(context: ExecutionContext) -> None:
    """Best-effort upload (D12): a storage outage never fails the node — the
    local copy stays and the maintenance reconciler re-uploads later."""
    (context.job_dir / "out.json").write_text("{}", encoding="utf-8")
    executor = _executor()
    executor._artifact_objects = _FakeArtifactStore(upload_error=RuntimeError("storage down"))

    result = executor._check_outputs(context)

    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)


def test_upload_mirrors_only_declared_expected_outputs(context: ExecutionContext) -> None:
    """Stray job_dir files are not mirrored — only expected_outputs that exist."""
    (context.job_dir / "out.json").write_text("{}", encoding="utf-8")
    (context.job_dir / "stray.txt").write_text("x", encoding="utf-8")
    store = _FakeArtifactStore()
    executor = _executor()
    executor._artifact_objects = store

    result = executor._check_outputs(context)

    assert result.status == "completed"
    assert store.uploaded == ["out.json"]


def test_execute_restores_evicted_inputs_before_sandboxed_run(
    context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targeted-rerun fallback (no online code Worker): an evicted declared
    input is restored from object storage before the sandboxed node runs."""
    _sandboxed(monkeypatch)
    payload = b"upstream-bytes"
    executor = _executor()
    executor._artifact_objects = _stored_input(payload)

    result = _run(
        executor,
        context,
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_bytes((job_dir / "upstream.json").read_bytes())
        """,
        inputs=("upstream.json",),
    )

    assert result.status == "completed"
    assert (context.job_dir / "out.json").read_bytes() == payload
