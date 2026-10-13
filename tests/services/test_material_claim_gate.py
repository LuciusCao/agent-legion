"""#1279 codex r7/r9：材料身份门禁的分档谓词与 claim 接线（协议 v6）。

统一不变量：无 hash 材料字节不得在无身份锚下被消费。档：
- hash 行（content_hash≠''）恒免（sha256 fail-closed 兜底）；
- 无 hash + etag≠'' → GATE_OLD_WORKER（拦 < v6）；
- 无 hash + etag='' → GATE_ALL（身份未建立，拦全部 Worker 等 binder）；
- 非材料/不可解析 → GATE_NEVER。
admission 接线（读相 claim_batch_select / 写相 evaluate_candidate 共用
admit_candidate）：旧 Worker skip 门禁任务（留队列不落失败）、其余照领、
升级后恢复；未绑定行对 v6 Worker 同样不可领。
"""

from __future__ import annotations

import json

from server.app.jobs.queries.material_claim_gate import (
    GATE_ALL,
    GATE_NEVER,
    GATE_OLD_WORKER,
)
from shared.protocol import MATERIAL_ETAG_PROTOCOL_VERSION
from tests.services import materials_testlib

WORKSPACE_ID = materials_testlib.WORKSPACE_ID
storage = materials_testlib.storage
service = materials_testlib.service


def _ready_hashless(job_db, service, storage, payload: bytes, *, with_etag: bool = True) -> str:
    result = service.presign(WORKSPACE_ID, filename="g.txt", size_bytes=len(payload))
    material_id = result["material"]["id"]
    (key, _) = storage.presigned[-1]
    storage.objects[key] = payload
    service.complete(WORKSPACE_ID, material_id)
    if not with_etag:
        with job_db.connect() as conn:
            conn.execute("update materials set object_etag='' where id=%s", (material_id,))
    return material_id


def _job_with_input(job_db, input_doc: dict, *, job_id: str = "job-gate") -> str:
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values (%s, %s, 'material', %s, 'queued', %s)",
            (job_id, WORKSPACE_ID, input_doc.get("material_id") or "", json.dumps(input_doc)),
        )
    return job_id


def _tier(job_db, input_doc: dict, *, job_id: str = "job-gate") -> str:
    return job_db.job_material_claim_gate_tier(
        WORKSPACE_ID, _job_with_input(job_db, input_doc, job_id=job_id)
    )


def test_tiers_follow_identity_state(job_db, service, storage) -> None:
    """谓词分档：无 hash+有 etag → old_worker；无 hash+无 etag → all；
    hash 行 → never。"""
    bound = _ready_hashless(job_db, service, storage, b"bound-bytes")
    assert _tier(job_db, {"type": "material", "material_id": bound}) == GATE_OLD_WORKER

    unbound = _ready_hashless(job_db, service, storage, b"unbound-bytes", with_etag=False)
    assert _tier(job_db, {"type": "material", "material_id": unbound}, job_id="j2") == GATE_ALL

    hashed = materials_testlib.presign_ready(service, storage, b"hashed-bytes")
    assert _tier(job_db, {"type": "material", "material_id": hashed["id"]}, job_id="j3") == (
        GATE_NEVER
    )


def test_non_material_inputs_are_never_gated(job_db, service, storage) -> None:
    assert _tier(job_db, {"type": "ref", "external_id": "q-1"}, job_id="j-ref") == GATE_NEVER
    assert _tier(job_db, {}, job_id="j-empty") == GATE_NEVER
    assert _tier(job_db, {"type": "material"}, job_id="j-noid") == GATE_NEVER  # 缺 material_id
    assert job_db.job_material_claim_gate_tier(WORKSPACE_ID, "job-missing") == GATE_NEVER


def test_bundle_members_take_the_highest_tier(job_db, service, storage) -> None:
    """bundle：任一成员命中则取其最高档（all > old_worker > never）。"""
    from server.app.services.material_bundles import MaterialBundlesService

    bundles = MaterialBundlesService(service.connect_source)
    bound_member = _ready_hashless(job_db, service, storage, b"bound-member")
    free = materials_testlib.presign_ready(service, storage, b"free-member")

    old_worker_bundle = bundles.create(
        WORKSPACE_ID, name="ow", members=[{"material_id": bound_member, "path": "a.txt"}]
    )
    assert _tier(
        job_db, {"type": "bundle", "bundle_id": old_worker_bundle["id"]}, job_id="jb1"
    ) == (GATE_OLD_WORKER)

    free_bundle = bundles.create(
        WORKSPACE_ID, name="free", members=[{"material_id": free["id"], "path": "b.txt"}]
    )
    assert _tier(job_db, {"type": "bundle", "bundle_id": free_bundle["id"]}, job_id="jb2") == (
        GATE_NEVER
    )


# --- admission 接线（读相 claim_batch_select / 写相 evaluate_candidate 共用
# admit_candidate，一处门禁两相生效）------------------------------------------


def _material_code_job(job_db, *, object_etag: str, content_hash: str = "") -> None:
    """test-workspace 下一个材料输入的 code claim（含排队请求）。"""
    from tests.helpers.agent_worker_api import broker, enqueue_code

    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('test-workspace', 'Test')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by, object_etag)"
            " values ('mat-gated', 'test-workspace', %s, 'g.txt', 'text/plain', 1,"
            " 'test-workspace/x/v2g0/m_mat-gated/g.txt', 'ready', 'user-1', %s)",
            (content_hash, object_etag),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values ('job-gated', 'test-workspace', 'material', 'mat-gated', 'queued', %s)",
            (json.dumps({"type": "material", "material_id": "mat-gated"}),),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('job-gated', 'package')")
    enqueue_code(broker(job_db.jobs_dir.parent), job_id="job-gated")


def _register_worker(**overrides) -> None:
    from server.app.agent_control.registry import AgentWorkerRegistry
    from tests.postgres_support import TEST_DATABASE_URL

    payload: dict = {
        "worker_id": "worker-1",
        "name": "worker",
        "runtimes": ["pi"],
        "max_concurrency": 10,
        "max_code_concurrency": 10,
        "labels": {},
        "protocol_version": MATERIAL_ETAG_PROTOCOL_VERSION,
    }
    payload.update(overrides)
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(**payload)


def _claims(job_db):
    from server.app.agent_broker.claim_batch import claim_batch
    from tests.helpers.agent_worker_api import broker

    return claim_batch(broker(job_db.jobs_dir.parent), "worker-1", None, None, limit=4)


def _queued(job_db, job_id: str) -> bool:
    with job_db.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where job_id=%s", (job_id,)
        ).fetchone()
    return row is not None and str(row["state"]) == "queued"


def test_old_worker_cannot_claim_etag_gated_material_job(job_db) -> None:
    """协议门禁（r7）：v5 Worker 领不到 old_worker 档任务（留队列不落
    失败），其余任务照领；升级 v6 后恢复可领。"""
    _material_code_job(job_db, object_etag="etag-1")
    # 对照组：非材料 code job（同池同行进）。
    from tests.helpers.agent_worker_api import broker, enqueue_code, insert_code_job_rows

    insert_code_job_rows(job_db, job_id="job-free")
    enqueue_code(broker(job_db.jobs_dir.parent), job_id="job-free")

    _register_worker(protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1)
    claims = _claims(job_db)
    # 旧 Worker：门禁任务留下，自由任务领到。
    assert [c.job_id for c in claims] == ["job-free"]
    assert _queued(job_db, "job-gated")
    assert not _queued(job_db, "job-free")

    # 升级到 v6（重新注册同 worker_id）后：门禁任务恢复可领。
    _register_worker()
    claims = _claims(job_db)
    assert any(c.job_id == "job-gated" for c in claims)
    assert not _queued(job_db, "job-gated")


def test_unbound_hashless_job_is_gated_for_all_workers(job_db) -> None:
    """r9 P1-2：etag='' 的无 hash 行（身份未建立、binder 在途）对所有
    Worker 都不可领——连 v6 也等绑定；绑定后（etag 落列）v6 可领。"""
    _material_code_job(job_db, object_etag="")
    _register_worker()  # v6

    assert _claims(job_db) == []
    assert _queued(job_db, "job-gated")

    # binder 落 etag 后：v6 可领。
    with job_db.connect() as conn:
        conn.execute("update materials set object_etag='etag-now' where id='mat-gated'")
    assert any(c.job_id == "job-gated" for c in _claims(job_db))


def test_gate_bypass_for_exempt_materials(job_db) -> None:
    """旁路格：hash 行（sha256 兜底）不受门禁，旧 Worker 照领。"""
    _material_code_job(job_db, object_etag="etag-1", content_hash="a" * 64)
    _register_worker(protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1)
    assert any(c.job_id == "job-gated" for c in _claims(job_db))
