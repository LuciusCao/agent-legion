"""Reconciler 批量预取（#714）：判定集合不变 + 语句数钉住。

``reupload_missing`` 原先逐 job ``get_job`` + active revision + 逐产物
``row_for_node``（O(jobs) 条独立查询/每小时一 pass）。改为每批预取 job 与
清单行、定义按快照文本/workspace 缓存后：

- 对照：同一数据集上，内嵌的改前算法（oracle）与新实现判定出的「需（重）
  上传」(job, node, name) 集合相等，且覆盖缺行 / 过期行 / 新鲜行 / 无
  job_dir / 路径不可映射 / 无 active revision / 窗口外 等分支；
- 语句数：新实现 = 1（窗口内节点）+ 2 × 批次数 + 回退 workspace 数，
  与 job 数无关；改前算法随 job 数线性增长。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.app.db.connection import DatabaseConnection
from server.app.db.schema import init_db
from server.app.jobs.queries import JobQueries
from server.app.services import job_artifact_maintenance
from server.app.services.job_artifact_gzip import row_stale
from server.app.services.job_artifact_maintenance import reupload_missing
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_errors import NotFoundError
from server.app.storage_paths import resolve_job_dir
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL

_JOBS = 450  # > 2 个 200-job 批次：跨批边界
_OUTPUTS = {"n1": ["a.json", "b.json"], "n2": ["c.json"]}
# ws-snap：job 自带快照；ws-live：回退 active revision；ws-none：无 revision。
_WORKSPACES = ("ws-snap", "ws-live", "ws-none")


@pytest.fixture(autouse=True)
def _schema() -> None:
    init_db(TEST_DATABASE_URL)


def _definition(nodes: dict[str, list[str]]) -> Any:
    return SimpleNamespace(
        nodes={key: SimpleNamespace(outputs=list(outputs)) for key, outputs in nodes.items()}
    )


@pytest.fixture
def definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot = the job's JSON ``{node: outputs}``; active revision per
    workspace (ws-none raises NotFoundError — the per-job skip family)."""

    def _from_snapshot(job: dict[str, Any]) -> Any:
        raw = job.get("workflow_definition_snapshot_json") or ""
        return _definition(json.loads(raw)) if raw else None

    def _active(job_db: Any, workspace_id: str, workflow_key: str) -> Any:
        job_db.get_active_workflow_revision(workspace_id, workflow_key)  # 真实读一次
        if workspace_id == "ws-none":
            raise NotFoundError("no active revision")
        return _definition({"n1": ["a.json", "b.json"]})

    monkeypatch.setattr(job_artifact_maintenance, "definition_from_job_snapshot", _from_snapshot)
    monkeypatch.setattr(job_artifact_maintenance, "require_workspace_active_definition", _active)


def _seed(job_db: JobQueries, jobs_dir: Path) -> None:
    """Deterministic mix per job index (see module docstring)."""
    with job_db.connect() as conn:
        for workspace in _WORKSPACES:
            conn.execute("insert into workspaces(id, name) values (%s, %s)", (workspace, workspace))
        for index in range(_JOBS):
            workspace = _WORKSPACES[index % 3]
            job_id = f"job-{index:04d}"
            snapshot = json.dumps(_OUTPUTS) if workspace == "ws-snap" else ""
            storage_dir = "/etc/unmappable" if index % 37 == 0 else f"jobs/{job_id}"
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, title, status,"
                " storage_dir, workflow_definition_snapshot_json)"
                " values (%s, %s, 's', %s, 't', 'completed', %s, %s)",
                (job_id, workspace, job_id, storage_dir, snapshot),
            )
            age = "30 days" if index % 41 == 0 else "1 hour"  # 窗口外
            for node_key in ("n1", "n2"):
                conn.execute(
                    "insert into node_runs(job_id, node_key, status, finished_at)"
                    " values (%s, %s, 'completed', now() - %s::interval)",
                    (job_id, node_key, age),
                )
            if index % 29 == 0:
                continue  # 无 job_dir
            job_dir = jobs_dir / job_id
            job_dir.mkdir(parents=True)
            for node_key, names in _OUTPUTS.items():
                for slot, name in enumerate(names):
                    payload = f"{job_id}/{name}".encode()
                    if (index + slot) % 5 == 4:
                        continue  # 本地无文件
                    (job_dir / name).write_bytes(payload)
                    state = (index + slot) % 4  # 0 缺行 1 新鲜 2 过期(size) 3 过期(hash)
                    if state == 0:
                        continue
                    recorded = (
                        payload
                        if state == 1
                        else payload + b"!"
                        if state == 2
                        else b"x" * len(payload)
                    )
                    conn.execute(
                        "insert into job_artifacts(job_id, node_key, name, storage_key,"
                        " size_bytes, content_hash) values (%s, %s, %s, %s, %s, %s)",
                        (
                            job_id,
                            node_key,
                            name,
                            f"jobs/{workspace}/{job_id}/{name}",
                            len(recorded),
                            hashlib.sha256(recorded).hexdigest(),
                        ),
                    )
        conn.execute("commit")


def _legacy_reupload_targets(
    store: JobArtifactObjectStore, job_db: JobQueries, jobs_dir: Path, window_days: int = 7
) -> set[tuple[str, str, str]]:
    """The pre-#714 per-job loop, verbatim in its reads, recording targets."""
    with job_db.read() as conn:
        rows = conn.execute(
            "select distinct job_id, node_key from node_runs"
            " where status='completed'"
            " and finished_at > now() - make_interval(days => %s)",
            (window_days,),
        ).fetchall()
    completed: dict[str, set[str]] = {}
    for row in rows:
        completed.setdefault(str(row["job_id"]), set()).add(str(row["node_key"]))
    targets: set[tuple[str, str, str]] = set()
    for job_id, node_keys in completed.items():
        job = job_db.get_job(job_id)
        if job is None:
            continue
        try:
            definition = job_artifact_maintenance.definition_from_job_snapshot(
                job
            ) or job_artifact_maintenance.require_workspace_active_definition(
                job_db, str(job["workspace_id"]), str(job["workspace_id"])
            )
        except job_artifact_maintenance._DEFINITION_FAILURES:
            continue
        try:
            job_dir = resolve_job_dir(job, jobs_dir)
        except job_artifact_maintenance._PATH_FAILURES:
            continue
        if not job_dir.is_dir():
            continue
        for node_key in node_keys:
            node = definition.nodes.get(node_key)
            if node is None:
                continue
            for name in node.outputs:
                local_path = job_dir / name
                if not local_path.is_file():
                    continue
                manifest_row = store.row_for_node(job_id, node_key, name)
                if manifest_row is not None and not row_stale(manifest_row, local_path):
                    continue
                targets.add((job_id, node_key, name))
    return targets


class _StatementCounter:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        real = DatabaseConnection.execute

        def _counting(conn: DatabaseConnection, sql: str, params: Any = None) -> Any:
            self.count += 1
            return real(conn, sql, params)

        monkeypatch.setattr(DatabaseConnection, "execute", _counting)


def _recording_store(monkeypatch: pytest.MonkeyPatch) -> tuple[JobArtifactObjectStore, list]:
    """Real manifest reads; uploads only recorded so both algorithms see the
    same manifest state (the decision set, not the upload side effect)."""
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    calls: list[tuple[str, str, str]] = []

    def _upload(**kwargs: Any) -> None:
        calls.append((kwargs["job_id"], kwargs["node_key"], kwargs["name"]))

    monkeypatch.setattr(store, "upload", _upload)
    return store, calls


def test_batched_reconciler_matches_legacy_decisions_with_constant_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, definitions: None
) -> None:
    jobs_dir = tmp_path / "jobs"
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir)
    _seed(job_db, jobs_dir)
    store, calls = _recording_store(monkeypatch)
    settings = SimpleNamespace(jobs_dir=jobs_dir)

    counter = _StatementCounter(monkeypatch)
    legacy = _legacy_reupload_targets(store, job_db, jobs_dir)
    legacy_statements, counter.count = counter.count, 0

    uploaded = reupload_missing(store, job_db, settings)

    assert set(calls) == legacy
    assert uploaded == len(calls) == len(legacy)
    # 数据集须同时覆盖「需上传」与「跳过」两侧，否则相等断言是空真。
    assert 100 < len(legacy) < _JOBS * 3
    in_window = _JOBS - len(range(0, _JOBS, 41))
    batches = -(-in_window // job_artifact_maintenance._REUPLOAD_BATCH_JOBS)
    fallback_workspaces = 2  # ws-live + ws-none（ws-snap 全走快照）
    assert counter.count == 1 + 2 * batches + fallback_workspaces
    assert legacy_statements > in_window  # 改前：至少每 job 一条


def test_statement_count_independent_of_job_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, definitions: None
) -> None:
    """Shrinking the batch size only adds 2 statements per extra batch —
    the per-job loop itself issues none."""
    jobs_dir = tmp_path / "jobs"
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir)
    _seed(job_db, jobs_dir)
    store, _calls = _recording_store(monkeypatch)
    settings = SimpleNamespace(jobs_dir=jobs_dir)
    counter = _StatementCounter(monkeypatch)

    counts = {}
    for batch_size in (100, 500):
        monkeypatch.setattr(job_artifact_maintenance, "_REUPLOAD_BATCH_JOBS", batch_size)
        counter.count = 0
        reupload_missing(store, job_db, settings)
        counts[batch_size] = counter.count

    in_window = _JOBS - len(range(0, _JOBS, 41))
    assert counts[500] == 1 + 2 * 1 + 2
    assert counts[100] == 1 + 2 * (-(-in_window // 100)) + 2


def test_definition_failure_replays_as_per_job_skip_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, definitions: None
) -> None:
    """ws-none's NotFoundError is read once, then every ws-none job skips —
    none of them uploads, the pass continues for the other workspaces."""
    jobs_dir = tmp_path / "jobs"
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir)
    _seed(job_db, jobs_dir)
    store, calls = _recording_store(monkeypatch)
    reads: list[str] = []
    real = job_db.get_active_workflow_revision

    def _spy(workspace_id: str, workflow_key: str) -> Any:
        reads.append(workspace_id)
        return real(workspace_id, workflow_key)

    monkeypatch.setattr(job_db, "get_active_workflow_revision", _spy)

    reupload_missing(store, job_db, SimpleNamespace(jobs_dir=jobs_dir))

    assert sorted(reads) == ["ws-live", "ws-none"]
    ws_none_jobs = {f"job-{index:04d}" for index in range(2, _JOBS, 3)}
    assert calls and not {job_id for job_id, _node, _name in calls} & ws_none_jobs
