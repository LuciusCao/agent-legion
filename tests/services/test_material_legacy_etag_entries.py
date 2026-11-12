"""#1316：存量无 hash 材料 × 消费入口（决策表 materials-and-runs-design §6.4.1）。

按入口参数化：

- 物化层 Host（单材料 / bundle / 分片，三者都经 build_runtime）与 Worker
  （claim 描述符 → worker 拉取，单材料 / bundle）：
  * 绑定前：size-only 照常消费——不挂起、不落 failed（base 语义）；
  * 绑定后：同尺寸覆盖在每个入口都被 If-Match 检出（MaterializeError）；
    ``bind=False`` 参数格就是「摘掉绑定」的对照——同一覆盖被静默消费。
- 旧 Worker 门禁（准入层 claim_admission 与描述符层同一谓词）：按行状态
  参数化，并断言两层结论一致（单一谓词）；R12 竞态（准入后 repair + 重传
  complete）由描述符层的同快照判定关闭。
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from server.app.executors._code_runtime import build_runtime
from server.app.executors.cancellation import CancellationToken
from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext
from server.app.services.material_bundles import MaterialBundlesService
from server.app.services.material_claim_descriptors import material_claim_block
from server.app.services.material_legacy_etag_binding import bind_legacy_material_etags
from shared.material_cache import MaterializeError
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION, PROTOCOL_VERSION
from tests.fakes.storage import etag_of
from tests.services import materials_testlib
from worker import bundle_fetch, material_fetch
from worker.material_fetch import materialize_claim_material

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service
legacy_hashless = materials_testlib.legacy_hashless

ORIGINAL = b"original-legacy-bytes"
POISON = b"poisoned-legacy-bytes"  # 同尺寸
assert len(ORIGINAL) == len(POISON)

ENTRIES = ["host_single", "host_bundle", "host_shard", "worker_single", "worker_bundle"]
OLD_WORKER = MATERIAL_ETAG_PROTOCOL_VERSION - 1


def _key(job_db, material_id: str) -> str:
    return materials_testlib.storage_key_of(job_db, material_id)


def _bundle(service, material_id: str) -> str:
    bundles = MaterialBundlesService(service.connect_source)
    return bundles.create(
        WORKSPACE_ID, name="legacy", members=[{"material_id": material_id, "path": "a.txt"}]
    )["id"]


def _input_doc(entry: str, material_id: str, bundle_id: str) -> dict[str, Any]:
    if entry.endswith("bundle"):
        return {"type": "bundle", "bundle_id": bundle_id}
    return {"type": "material", "material_id": material_id}


def _job(input_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "job-legacy",
        "workspace_id": WORKSPACE_ID,
        "run_id": "",
        "input_json": json.dumps(input_doc),
    }


def _host_consume(job_db, storage, cache_root: Path, entry: str, input_doc: dict) -> bytes:
    executor = CodeExecutor(repo_root=cache_root, job_db=job_db, materials_cache_root=cache_root)
    executor._storage_probed = True
    executor._object_storage = storage
    context = ExecutionContext(
        execution_id="exec-legacy",
        lease_id="lease-1",
        node_run_id=1,
        executor_id="code",
        workspace_id=WORKSPACE_ID,
        job_id="job-legacy",
        workflow_key="wf",
        node_key="intake",
        capability="intake_items",
        workspace={"id": WORKSPACE_ID},
        job=_job(input_doc),
        job_dir=cache_root / "job",
        log_path=cache_root / "job" / "run.log",
        inputs=(),
        expected_outputs=(),
        # 分片：本地 shard 与普通节点同走 build_runtime（shard 只多 runtime 字段）。
        runtime={"shard_index": 0, "shard_input": {}} if entry == "host_shard" else {},
    )
    runtime = build_runtime(executor, context, CancellationToken(threading.Event()))
    path = Path(runtime["materials"]["path"])
    return (path / "a.txt").read_bytes() if path.is_dir() else path.read_bytes()


def _fake_presigned_get(monkeypatch, storage) -> None:
    """Worker 的 presigned GET：按 URL 中的 key 读假存储，If-Match 不符 → 412 语义。"""

    def _open(url: str, *, if_match: str = "") -> io.BytesIO:
        key = url.split("/download/", 1)[1]
        payload = storage.objects[key]
        if if_match and if_match != etag_of(payload):
            raise MaterializeError("material bytes changed after completion (ETag mismatch)")
        return io.BytesIO(payload)

    monkeypatch.setattr(material_fetch, "_open_download", _open)
    # bundle_fetch 在导入时绑定了 _open_download 名字：两处都替换。
    monkeypatch.setattr(bundle_fetch, "_open_download", _open)


def _worker_consume(job_db, storage, work_root: Path, input_doc: dict) -> bytes:
    descriptor = material_claim_block(
        job_db,
        WORKSPACE_ID,
        _job(input_doc),
        storage=storage,
        worker_protocol_version=PROTOCOL_VERSION,
    )
    manifest = {"runtime_context": {"material": descriptor}}
    block = materialize_claim_material(manifest, work_root / "exec-1")
    assert block is not None
    path = Path(block["path"])
    return (path / "a.txt").read_bytes() if path.is_dir() else path.read_bytes()


def _consume(entry, job_db, storage, root: Path, input_doc: dict) -> bytes:
    if entry.startswith("host"):
        return _host_consume(job_db, storage, root, entry, input_doc)
    return _worker_consume(job_db, storage, root, input_doc)


@pytest.mark.parametrize("entry", ENTRIES)
def test_unbound_legacy_row_is_consumed_size_only_without_suspension(
    job_db, service, storage, tmp_path, monkeypatch, entry
) -> None:
    """绑定前（及绑定失败的行）：每个入口照常 size-only 消费，不挂起不失败。"""
    _fake_presigned_get(monkeypatch, storage)
    material_id = legacy_hashless(job_db, service, storage, ORIGINAL, filename="a.txt")
    bundle_id = _bundle(service, material_id)

    got = _consume(entry, job_db, storage, tmp_path, _input_doc(entry, material_id, bundle_id))

    assert got == ORIGINAL


@pytest.mark.parametrize("bind", [True, False], ids=["bound", "mutation-no-binding"])
@pytest.mark.parametrize("entry", ENTRIES)
def test_binding_turns_a_live_url_overwrite_into_a_loud_failure(
    job_db, service, storage, tmp_path, monkeypatch, entry, bind
) -> None:
    """绑定后：活 URL 持有者的同尺寸覆盖在每个入口都 fail-closed（If-Match）。
    ``mutation-no-binding`` 格摘掉绑定：同一覆盖在同一入口被静默消费。"""
    _fake_presigned_get(monkeypatch, storage)
    material_id = legacy_hashless(job_db, service, storage, ORIGINAL, filename="a.txt")
    bundle_id = _bundle(service, material_id)
    if bind:
        assert bind_legacy_material_etags(job_db, storage).bound == [material_id]
    storage.objects[_key(job_db, material_id)] = POISON  # 升级前签出、仍有效的 PUT URL 重放

    input_doc = _input_doc(entry, material_id, bundle_id)
    if bind:
        with pytest.raises(MaterializeError, match="ETag mismatch"):
            _consume(entry, job_db, storage, tmp_path, input_doc)
    else:
        assert _consume(entry, job_db, storage, tmp_path, input_doc) == POISON


# --- 旧 Worker 门禁：准入层与描述符层同一谓词 ---------------------------------


def _set_state(job_db, service, storage, material_id: str, state: str) -> None:
    # binder 自开连接，不能与下面的 conn 块嵌套（DB-POOL-CHECKOUT-001）：
    # 顺序检出——先绑定，再按需落行改写。
    if state in ("bound", "repaired_reuploaded"):
        bind_legacy_material_etags(job_db, storage)
    with job_db.connect() as conn:
        if state == "complete_bound":
            conn.execute("update materials set object_etag='etag-new' where id=%s", (material_id,))
        elif state in ("uploading", "failed", "expired"):
            conn.execute("update materials set status=%s where id=%s", (state, material_id))
    if state == "repaired_reuploaded":
        outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
        storage.objects[str(outcome["storage_key"])] = ORIGINAL
        service.complete(
            WORKSPACE_ID, material_id, expected_upload_generation=outcome["upload_generation"]
        )


# (状态, 旧 Worker 是否被门禁)
GATE_CELLS = [
    ("unbound", False),  # 无身份可验：base 语义
    ("bound", True),  # binder 绑定的存量行：永久门禁（codex r2 P1，无排空期）
    ("complete_bound", True),  # complete 绑定的行（非存量）：恒拦
    ("repaired_reuploaded", True),  # repair 重传落新 etag：恒拦
    ("uploading", True),  # R12：repair 窗口
    ("failed", False),  # 维持既有失败语义，不永久卡住
    ("expired", False),
]


def _job_row(job_db, input_doc: dict, job_id: str) -> str:
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values (%s, %s, 'material', '', 'queued', %s)",
            (job_id, WORKSPACE_ID, json.dumps(input_doc)),
        )
    return job_id


@pytest.mark.parametrize("kind", ["material", "bundle"])
@pytest.mark.parametrize(("state", "gated"), GATE_CELLS)
def test_old_worker_gate_is_one_predicate_at_admission_and_descriptor(
    job_db, service, storage, kind, state, gated
) -> None:
    material_id = legacy_hashless(job_db, service, storage, ORIGINAL, filename="a.txt")
    bundle_id = _bundle(service, material_id)
    _set_state(job_db, service, storage, material_id, state)
    input_doc = (
        {"type": "bundle", "bundle_id": bundle_id}
        if kind == "bundle"
        else {"type": "material", "material_id": material_id}
    )

    admission = job_db.job_material_inputs_require_etag_protocol(
        WORKSPACE_ID, _job_row(job_db, input_doc, f"job-{kind}-{state}")
    )
    assert admission is gated

    if state in ("uploading", "failed", "expired"):
        # 描述符层对非 ready 行恒拒（not ready，既有语义），与 Worker 版本无关。
        with pytest.raises(MaterializeError, match="not ready"):
            material_claim_block(
                job_db,
                WORKSPACE_ID,
                _job(input_doc),
                storage=storage,
                worker_protocol_version=OLD_WORKER,
            )
        return
    if gated:
        with pytest.raises(MaterializeError, match="requires Worker protocol"):
            material_claim_block(
                job_db,
                WORKSPACE_ID,
                _job(input_doc),
                storage=storage,
                worker_protocol_version=OLD_WORKER,
            )
    else:
        assert material_claim_block(
            job_db,
            WORKSPACE_ID,
            _job(input_doc),
            storage=storage,
            worker_protocol_version=OLD_WORKER,
        )
    # v6+ Worker 不受门禁。
    assert material_claim_block(
        job_db,
        WORKSPACE_ID,
        _job(input_doc),
        storage=storage,
        worker_protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION,
    )


@pytest.mark.parametrize("race", ["repair_and_reupload", "binder_binds"])
def test_r12_admission_to_descriptor_window_is_closed_on_the_snapshot(
    job_db, service, storage, race
) -> None:
    """R12：旧 Worker 准入时行是 etag=''（放行）；描述符组装前 repair + 重传
    complete（或 binder 绑定）落了新 etag——描述符层按同一行快照拒绝，
    旧 Worker 拿不到它会忽略 etag 的新 URL。"""
    material_id = legacy_hashless(job_db, service, storage, ORIGINAL, filename="a.txt")
    input_doc = {"type": "material", "material_id": material_id}
    assert not job_db.job_material_inputs_require_etag_protocol(
        WORKSPACE_ID, _job_row(job_db, input_doc, "job-r12")
    )

    if race == "repair_and_reupload":
        outcome = service.request_material_reupload(WORKSPACE_ID, material_id)
        storage.objects[str(outcome["storage_key"])] = ORIGINAL
        service.complete(
            WORKSPACE_ID, material_id, expected_upload_generation=outcome["upload_generation"]
        )
    else:
        bind_legacy_material_etags(job_db, storage)

    with pytest.raises(MaterializeError, match="requires Worker protocol"):
        material_claim_block(
            job_db,
            WORKSPACE_ID,
            _job(input_doc),
            storage=storage,
            worker_protocol_version=OLD_WORKER,
        )


def test_claim_response_forwards_the_worker_protocol_version(job_db, service, storage) -> None:
    """接线：claim 响应路径（resolve_code_runtime_context）把 Worker 协议版本
    传到描述符层——旧 Worker 对已绑未放行行的 claim 以 500 回收（不下发描述符）。"""
    from types import SimpleNamespace

    from server.app.agent_broker.code_manifest import resolve_code_runtime_context

    material_id = legacy_hashless(job_db, service, storage, ORIGINAL, filename="a.txt")
    bind_legacy_material_etags(job_db, storage)
    job_id = _job_row(job_db, {"type": "material", "material_id": material_id}, "job-wire")
    manifest = {"job_id": job_id, "workspace_id": WORKSPACE_ID, "runtime_context": {}}

    with pytest.raises(MaterializeError, match="requires Worker protocol"):
        resolve_code_runtime_context(
            manifest,
            job_db,
            {},
            SimpleNamespace(storage=storage),
            worker_protocol_version=OLD_WORKER,
        )


@pytest.mark.parametrize("entry", ENTRIES)
def test_binding_orphans_caches_filled_before_binding(
    job_db, service, storage, tmp_path, monkeypatch, entry
) -> None:
    """#1319 codex R1：未绑窗口内（首轮 HEAD 失败 / binder 尚未扫到）缓存了
    A，随后活 URL 把对象覆盖成 B，binder 绑定 B。缓存命中不重验——绑定必须
    使旧缓存地址孤儿化（代次 +1），否则节点继续静默消费与绑定身份不符的 A。"""
    _fake_presigned_get(monkeypatch, storage)
    material_id = legacy_hashless(job_db, service, storage, ORIGINAL, filename="a.txt")
    bundle_id = _bundle(service, material_id)
    input_doc = _input_doc(entry, material_id, bundle_id)
    assert _consume(entry, job_db, storage, tmp_path, input_doc) == ORIGINAL  # 缓存 A

    storage.objects[_key(job_db, material_id)] = POISON
    assert bind_legacy_material_etags(job_db, storage).bound == [material_id]

    # 同一缓存根：消费的必须是绑定身份对应的字节，而不是绑定前缓存的 A。
    assert _consume(entry, job_db, storage, tmp_path, input_doc) == POISON
