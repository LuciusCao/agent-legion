"""#1279 codex r7 P1：材料 ETag 协议门禁的判定谓词（claim 侧）。

钉 MaterialClaimGateQueriesMixin.job_material_inputs_require_etag_protocol
的四格：无 hash + 有 etag → 拦；hash + 有 etag → 放行（旧 Worker sha256
兜底）；etag='' → 放行（无 etag 可验）；非材料输入/不可解析 → 放行。
bundle 成员同谓词。admission 接线（低于 v6 的 Worker skip）由
test_material_claim_gate_admission 覆盖。
"""

from __future__ import annotations

import json

import pytest

from server.app.jobs.queries.material_claim_gate import LEGACY_URL_DRAIN_SECONDS
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


def _gated(job_db, input_doc: dict, *, job_id: str = "job-gate") -> bool:
    return job_db.job_material_inputs_require_etag_protocol(
        WORKSPACE_ID, _job_with_input(job_db, input_doc, job_id=job_id)
    )


def test_hashless_row_with_etag_is_gated(job_db, service, storage) -> None:
    material_id = _ready_hashless(job_db, service, storage, b"gated-bytes")
    assert _gated(job_db, {"type": "material", "material_id": material_id})


def test_hash_row_with_etag_is_exempt(job_db, service, storage) -> None:
    """hash 行：旧 Worker 有 sha256 fail-closed 兜底，门禁豁免（可用性）。"""
    ready = materials_testlib.presign_ready(service, storage, b"hashed-bytes")
    assert not _gated(job_db, {"type": "material", "material_id": ready["id"]})


def test_hashless_row_without_etag_is_exempt(job_db, service, storage) -> None:
    """etag=''（存量行/旧服务）：无内容身份可验，门禁无意义，放行。"""
    material_id = _ready_hashless(job_db, service, storage, b"legacy-bytes", with_etag=False)
    assert not _gated(job_db, {"type": "material", "material_id": material_id})


def test_non_material_inputs_are_never_gated(job_db, service, storage) -> None:
    assert not _gated(job_db, {"type": "ref", "external_id": "q-1"}, job_id="job-ref")
    assert not _gated(job_db, {}, job_id="job-empty")
    assert not _gated(job_db, {"type": "material"}, job_id="job-no-id")  # 缺 material_id
    assert not job_db.job_material_inputs_require_etag_protocol(WORKSPACE_ID, "job-missing")


def test_bundle_members_follow_the_same_predicate(job_db, service, storage) -> None:
    """bundle：任一成员命中谓词即拦；全成员豁免（hash/无 etag）则放行。"""
    from server.app.services.material_bundles import MaterialBundlesService

    bundles = MaterialBundlesService(service.connect_source)
    gated_member = _ready_hashless(job_db, service, storage, b"gated-member")
    free = materials_testlib.presign_ready(service, storage, b"free-member")

    gated_bundle = bundles.create(
        WORKSPACE_ID, name="gated", members=[{"material_id": gated_member, "path": "a.txt"}]
    )
    assert _gated(job_db, {"type": "bundle", "bundle_id": gated_bundle["id"]}, job_id="job-bg")

    free_bundle = bundles.create(
        WORKSPACE_ID, name="free", members=[{"material_id": free["id"], "path": "b.txt"}]
    )
    assert not _gated(job_db, {"type": "bundle", "bundle_id": free_bundle["id"]}, job_id="job-bf")


# --- admission 接线（读相 claim_batch_select / 写相 evaluate_candidate 共用
# admit_candidate，一处门禁两相生效）------------------------------------------


def _gated_code_job(job_db, *, object_etag: str, content_hash: str = "") -> None:
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
    """协议门禁（#1279 codex r7）：v5 Worker 领不到命中谓词的材料任务
    （留队列不落失败），其余任务照领；升级 v6 后恢复可领。"""
    _gated_code_job(job_db, object_etag="etag-1")
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


def test_gate_bypass_for_exempt_materials(job_db) -> None:
    """旁路格：hash 行（sha256 兜底）与 etag='' 行不受门禁，旧 Worker 照领。"""
    _gated_code_job(job_db, object_etag="etag-1", content_hash="a" * 64)
    _register_worker(protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1)
    assert any(c.job_id == "job-gated" for c in _claims(job_db))


# --- #1316：存量行绑定后的旧 Worker 准入（经 claim_batch 全链）-----------------


def _set_gated_row(job_db, **fields) -> None:
    assignments = ", ".join(f"{name}={value}" for name, value in fields.items())
    with job_db.connect() as conn:
        conn.execute(f"update materials set {assignments} where id='mat-gated'")


@pytest.mark.parametrize(
    ("fields", "old_worker_claims"),
    [
        # binder 刚绑定：升级前签出的 URL 可能仍活 → 旧 Worker 跳过。
        ({"legacy_etag_bound_at": "now()"}, False),
        # 绑定时刻 + TTL + 裕度已过：存量 URL 全部过期 → 旧 Worker 照领。
        (
            {
                "legacy_etag_bound_at": "now() - make_interval(secs => "
                f"{LEGACY_URL_DRAIN_SECONDS + 1})"
            },
            True,
        ),
        # R12：repair 窗口（uploading）→ 跳过（不领到再 500）。
        ({"status": "'uploading'", "object_etag": "''"}, False),
        # failed 维持既有失败语义（领到 → 描述符 not ready → 有界 requeue 后 failed），
        # 不被旧 Worker 永久跳过。
        ({"status": "'failed'"}, True),
    ],
    ids=["bound_fresh", "bound_drained", "uploading", "failed"],
)
def test_old_worker_admission_for_legacy_bound_rows(job_db, fields, old_worker_claims) -> None:
    _gated_code_job(job_db, object_etag="etag-1")
    _set_gated_row(job_db, **fields)
    _register_worker(protocol_version=MATERIAL_ETAG_PROTOCOL_VERSION - 1)

    claimed = any(c.job_id == "job-gated" for c in _claims(job_db))

    assert claimed is old_worker_claims
    assert _queued(job_db, "job-gated") is (not old_worker_claims)


def test_drain_window_covers_the_material_presign_ttl() -> None:
    """放行时刻的 TTL 项必须与材料 presign PUT 的实际有效期一致（改了一边
    忘了另一边，旧 Worker 会在活 URL 仍有效时被放行）。"""
    from server.app.jobs.queries import material_claim_gate
    from server.app.services import materials

    assert material_claim_gate.PRESIGN_PUT_TTL_SECONDS == materials._PRESIGN_EXPIRY_SECONDS
    assert material_claim_gate.LEGACY_URL_DRAIN_SECONDS > materials._PRESIGN_EXPIRY_SECONDS
