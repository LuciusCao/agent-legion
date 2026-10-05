"""validator 不得改写 remote 通道输出（#867，#855 codex R3 follow-up）。

Worker 以 dict-form S3 ref 返回的产物在 Host 侧校验之前已被
``apply_worker_artifact_refs`` 提升为权威对象并登记清单行，校验后的镜像又
以 ``skip=remote_names`` 跳过它们。修复前 validator 改写这些文件只落本地：
节点按改写后的本地副本判完成，而对象存储与清单哈希仍是校验前字节。契约
（EXEC-VALIDATION-001）：remote 通道输出对 validator 只读，校验前后按内容
哈希比对，被改写/删除即判败（``Validator error:`` 通道，点名产物）。

本地（归档 / str ref）通道的 reconcile 语义不变——见本文件末尾的混合用例
与 tests/workflows/test_output_validation_view.py。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome
from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifact_raw import open_raw_artifact
from tests.fakes.artifact_keys import pin_legacy_authority_keys
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.skill_manager import _make_skill_manager
from tests.postgres_support import TEST_DATABASE_URL

PAYLOAD = b'{"raw": "  worker bytes  "}'
HASH = hashlib.sha256(PAYLOAD).hexdigest()
STAGING_KEY = "jobs-staging/ws-1/job-1/exec-1/out.json"
AUTHORITY_KEY = "jobs/ws-1/job-1/out.json"
SKILL = "wf/node_a"

_VALIDATE_READ_ONLY = (
    "import pathlib, sys\nassert (pathlib.Path(sys.argv[1]) / 'out.json').read_text()\n"
)
_VALIDATE_REWRITE_IN_PLACE = (
    "import pathlib, sys\n"
    "(pathlib.Path(sys.argv[1]) / 'out.json').write_text('{\"cleaned\": true}')\n"
)
_VALIDATE_REWRITE_REPLACE = (
    "import os, pathlib, sys\n"
    "view = pathlib.Path(sys.argv[1])\n"
    "(view / 'tmp.json').write_text('{\"cleaned\": true}')\n"
    "os.replace(view / 'tmp.json', view / 'out.json')\n"
)
_VALIDATE_DELETE = "import pathlib, sys\n(pathlib.Path(sys.argv[1]) / 'out.json').unlink()\n"
_VALIDATE_SAME_BYTES_REPLACE = (
    "import os, pathlib, sys\n"
    "view = pathlib.Path(sys.argv[1])\n"
    "(view / 'tmp.json').write_bytes((view / 'out.json').read_bytes())\n"
    "os.replace(view / 'tmp.json', view / 'out.json')\n"
)
_VALIDATE_CLEAN_LOCAL_ONLY = (
    "import pathlib, sys\n"
    "(pathlib.Path(sys.argv[1]) / 'local.json').write_text('{\"local\": \"cleaned\"}')\n"
)


@pytest.fixture(autouse=True)
def _legacy_fixed_authority_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """钉回固定权威 key 布局，便于直接按 key 断言对象字节（同姊妹文件）。"""
    pin_legacy_authority_keys(monkeypatch)


class _StubJobDb:
    def __init__(self, job: dict[str, Any]) -> None:
        self._job = job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._job if job_id == self._job["id"] else None


class _StubLeases:
    def __init__(self, job: dict[str, Any]) -> None:
        self.job_db = _StubJobDb(job)
        self.data_dir = None
        self.results: list[Any] = []

    def finish(self, lease_id: str, result: Any, *, stage_timer: Any = None) -> bool:
        _ = stage_timer
        self.results.append(result)
        return True


class _StubArtifactStore:
    def add_ref(self, job_id: str, node_key: str, name: str, digest: str) -> None:
        _ = (job_id, node_key, name, digest)


def _make_handler(
    tmp_path: Path, storage: FakeObjectStorage, validate_script: str
) -> tuple[AgentCompletionHandler, _StubLeases, JobArtifactObjectStore, Path]:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('ws-1', 'ws') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, "
            " title, status, storage_dir) values ('job-1', 'ws-1', 's', 's1', 't', 'pending', 'd')"
        )
        cursor = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values ('job-1', 'node_a', 'running', '[]', '', '', '', current_timestamp)"
            " returning id"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id,"
            " job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)"
            " values ('lease-1', 'exec-1', 'agent:worker-1', 'ws-1', 'job-1', 'node_a', %s,"
            " 'active', current_timestamp, current_timestamp,"
            " current_timestamp + interval '1 hour')",
            (cursor.fetchone()["id"],),
        )
    jobs_dir = tmp_path / "jobs"
    job = {"id": "job-1", "workspace_id": "ws-1", "storage_dir": "jobs/ws/job-1"}
    job_dir = jobs_dir / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    leases = _StubLeases(job)
    object_store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    handler = AgentCompletionHandler(
        leases,  # type: ignore[arg-type]
        _StubArtifactStore(),  # type: ignore[arg-type]
        jobs_dir,
        tmp_path / "bundles",
        skill_manager=_make_skill_manager(tmp_path, SKILL, validate_script),
        object_store=object_store,
    )
    return handler, leases, object_store, job_dir


def _remote_ref() -> dict[str, Any]:
    return {"storage_key": STAGING_KEY, "size_bytes": len(PAYLOAD), "content_hash": HASH}


def _finish(
    handler: AgentCompletionHandler,
    artifacts: dict[str, Any],
    expected: tuple[str, ...] = ("out.json",),
    inputs: tuple[str, ...] = (),
) -> None:
    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={
            "expected_outputs": list(expected),
            "inputs": list(inputs),
            "execution_id": "exec-1",
            "skill": SKILL,
        },
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )


def _storage() -> FakeObjectStorage:
    storage = FakeObjectStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    return storage


def test_read_only_validator_on_remote_output_completes(tmp_path: Path) -> None:
    storage = _storage()
    handler, leases, object_store, job_dir = _make_handler(tmp_path, storage, _VALIDATE_READ_ONLY)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    assert storage.objects[AUTHORITY_KEY] == PAYLOAD
    row = object_store.lookup("job-1", "out.json")
    assert row is not None and row["content_hash"] == HASH


def test_remote_output_snapshot_reuses_promote_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex #913 P2：校验前快照取提升阶段已流式算出的摘要，不再整读产物；
    只在校验通过后复算一次。"""
    import server.app.workflows.remote_output_guard as guard

    hashed: list[str] = []
    real_sha256 = guard.file_sha256
    monkeypatch.setattr(
        guard, "file_sha256", lambda path: hashed.append(path.name) or real_sha256(path)
    )
    snapshots: list[dict[str, str]] = []
    real_find = guard.find_remote_output_rewrites
    monkeypatch.setattr(
        guard,
        "find_remote_output_rewrites",
        lambda view, snapshot: snapshots.append(dict(snapshot)) or real_find(view, snapshot),
    )
    storage = _storage()
    handler, leases, _, _ = _make_handler(tmp_path, storage, _VALIDATE_READ_ONLY)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert snapshots == [{"out.json": HASH}]
    assert hashed == ["out.json"]  # 只有校验后的一次复算


@pytest.mark.parametrize(
    ("script", "change"),
    [
        (_VALIDATE_REWRITE_IN_PLACE, "rewritten"),
        (_VALIDATE_REWRITE_REPLACE, "rewritten"),
        (_VALIDATE_DELETE, "deleted"),
    ],
    ids=["in-place", "replace", "delete"],
)
def test_validator_modifying_remote_output_fails_the_node(
    tmp_path: Path, script: str, change: str
) -> None:
    """修复前：节点 completed，而权威对象 / 清单哈希仍是校验前字节。"""
    storage = _storage()
    handler, leases, object_store, job_dir = _make_handler(tmp_path, storage, script)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed", (
        "validator modified a remote-channel output but the node completed; "
        f"authority object still holds {storage.objects.get(AUTHORITY_KEY)!r}"
    )
    assert result.exit_code == 1
    assert result.error_message.startswith("Validator error: ")
    assert "remote-channel" in result.error_message
    assert f"'out.json' ({change})" in result.error_message
    # 权威对象与清单保持 Worker 上传的已核验字节，未被任何一方改写。
    assert storage.objects[AUTHORITY_KEY] == PAYLOAD
    row = object_store.lookup("job-1", "out.json")
    assert row is not None and row["content_hash"] == HASH
    _assert_reads_serve_worker_bytes(job_dir, object_store)


def _assert_reads_serve_worker_bytes(job_dir: Path, object_store: JobArtifactObjectStore) -> None:
    """codex #913 R2 P1：改写过的本地副本被逐出，本地优先的读路径回落到
    对象存储权威副本，拿到的是 Worker 原始字节而非 validator 改写。"""
    assert not (job_dir / "out.json").exists()
    raw = open_raw_artifact(job_dir / "out.json", object_store, "job-1", "out.json")
    assert raw.path is None and raw.stream is not None
    with raw.stream as stream:
        assert stream.read() == PAYLOAD


def test_failing_validator_that_also_rewrites_keeps_its_verdict_and_evicts(
    tmp_path: Path,
) -> None:
    """validator 自身判败但已原地改写：保留它的失败消息，改写副本同样逐出。"""
    storage = _storage()
    script = _VALIDATE_REWRITE_IN_PLACE + "print('bad output', file=sys.stderr)\nsys.exit(1)\n"
    handler, leases, object_store, job_dir = _make_handler(tmp_path, storage, script)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert result.error_message.startswith("Output validation failed: bad output")
    _assert_reads_serve_worker_bytes(job_dir, object_store)


def test_input_read_only_violation_that_also_rewrites_keeps_its_verdict_and_evicts(
    tmp_path: Path,
) -> None:
    """#939：validator 同时改写声明 input 与 remote 输出——视图出口的 input
    只读检查抛错（异常路径）时，原错误保留，remote 改写副本同样逐出。"""
    storage = _storage()
    script = _VALIDATE_REWRITE_IN_PLACE + (
        "(pathlib.Path(sys.argv[1]) / 'in.json').write_text('{\"mutated\": true}')\n"
    )
    handler, leases, object_store, job_dir = _make_handler(tmp_path, storage, script)
    (job_dir / "in.json").write_text('{"in": 1}', encoding="utf-8")

    _finish(handler, {"out.json": _remote_ref()}, inputs=("in.json",))

    result = leases.results[0]
    assert result.status == "failed"
    assert result.error_message.startswith("Validator error: ")
    assert "mutated declared input 'in.json'" in result.error_message
    assert "remote-channel" not in result.error_message
    assert storage.objects[AUTHORITY_KEY] == PAYLOAD
    row = object_store.lookup("job-1", "out.json")
    assert row is not None and row["content_hash"] == HASH
    _assert_reads_serve_worker_bytes(job_dir, object_store)


def test_unhashable_remote_output_never_skips_eviction_of_the_others(tmp_path: Path) -> None:
    """codex #1031 R1：改写 A、令排序靠后的 B 不可读、并触发 input 只读错误——
    B 记为无法验证（视为分歧），A、B 的本地副本都被逐出，原错误保留。"""
    payload_b = b'{"b": "worker bytes"}'
    staging_b = "jobs-staging/ws-1/job-1/exec-1/zz.json"
    storage = _storage()
    storage.objects[staging_b] = payload_b
    script = _VALIDATE_REWRITE_IN_PLACE + (
        "import os\n"
        "os.chmod(pathlib.Path(sys.argv[1]) / 'zz.json', 0)\n"
        "(pathlib.Path(sys.argv[1]) / 'in.json').write_text('{\"mutated\": true}')\n"
    )
    handler, leases, object_store, job_dir = _make_handler(tmp_path, storage, script)
    (job_dir / "in.json").write_text('{"in": 1}', encoding="utf-8")
    ref_b = {
        "storage_key": staging_b,
        "size_bytes": len(payload_b),
        "content_hash": hashlib.sha256(payload_b).hexdigest(),
    }

    _finish(
        handler,
        {"out.json": _remote_ref(), "zz.json": ref_b},
        expected=("out.json", "zz.json"),
        inputs=("in.json",),
    )

    result = leases.results[0]
    assert result.status == "failed"
    assert "mutated declared input 'in.json'" in result.error_message
    _assert_reads_serve_worker_bytes(job_dir, object_store)
    assert not (job_dir / "zz.json").exists()
    raw = open_raw_artifact(job_dir / "zz.json", object_store, "job-1", "zz.json")
    assert raw.path is None and raw.stream is not None
    with raw.stream as stream:
        assert stream.read() == payload_b


def test_eviction_failure_never_replaces_the_original_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#939：逐出自身出错只记日志，不吞掉 / 覆盖原校验错误。"""
    real_unlink = Path.unlink

    def _unlink_boom(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == "out.json":
            raise RuntimeError("evict exploded")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink_boom)
    storage = _storage()
    script = _VALIDATE_REWRITE_IN_PLACE + "print('bad output', file=sys.stderr)\nsys.exit(1)\n"
    handler, leases, _, _ = _make_handler(tmp_path, storage, script)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert result.error_message.startswith("Output validation failed: bad output")


def test_validator_replacing_remote_output_with_identical_bytes_completes(
    tmp_path: Path,
) -> None:
    """按内容哈希判定：换 inode 但字节不变不算改写。"""
    storage = _storage()
    handler, leases, _, _ = _make_handler(tmp_path, storage, _VALIDATE_SAME_BYTES_REPLACE)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert storage.objects[AUTHORITY_KEY] == PAYLOAD


def test_local_channel_output_cleaning_still_reconciles_beside_remote(tmp_path: Path) -> None:
    """本地通道不回归：同一节点的 str-ref 输出照常被清洗并镜像清洗后字节。"""
    storage = _storage()
    handler, leases, _, job_dir = _make_handler(tmp_path, storage, _VALIDATE_CLEAN_LOCAL_ONLY)
    (job_dir / "local.json").write_text('{"local": "raw"}', encoding="utf-8")

    _finish(
        handler,
        {"out.json": _remote_ref(), "local.json": "sha256:abc"},
        expected=("out.json", "local.json"),
    )

    assert leases.results[0].status == "completed"
    cleaned = b'{"local": "cleaned"}'
    assert (job_dir / "local.json").read_bytes() == cleaned
    assert storage.objects["jobs/ws-1/job-1/local.json"] == cleaned
    assert storage.objects[AUTHORITY_KEY] == PAYLOAD
