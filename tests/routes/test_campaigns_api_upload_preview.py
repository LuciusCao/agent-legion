"""Campaigns API: submit upload/inline channel + preview endpoints (#532 PR-A).

Split out of tests/routes/test_campaigns_api.py (PR #541 round-3 P1: the
single file passed the 800-line split threshold). Covers the two JSON-body
faces the round-2/round-3 review fixes hardened — the submit manifest
channels (inline items / multipart upload / RunItem contract / size gates)
and the preview dry-run endpoints (rerun / upgrade / submit judgements and
the knob guards they share with creation).
"""

from __future__ import annotations

import pytest

from server.app.routes.campaigns import _read_upload_size

CSRF = {"x-agent-legion-request": "1"}

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]

_CREATE_COUNT = 0


def _create_workspace(client, job_db) -> str:
    global _CREATE_COUNT
    _CREATE_COUNT += 1
    ws_id = "campaign_api_ws" if _CREATE_COUNT == 1 else f"campaign_api_ws_{_CREATE_COUNT}"
    response = client.post("/api/workspaces", json={"id": ws_id, "name": "Campaign WS"})
    assert response.status_code == 200, response.text
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(job_db, ws_id)
    return ws_id


@pytest.fixture(autouse=True)
def _reset_create_count():
    global _CREATE_COUNT
    _CREATE_COUNT = 0
    yield


def _seed_failed_jobs(client, job_db, workspace_id: str, count: int) -> list[str]:
    batch = job_db.create_run(
        workspace_id,
        "batch_by_ids",
        {"question_ids": [f"Q{i}" for i in range(count)]},
        workspace_id=workspace_id,
    )
    ids: list[str] = []
    for i in range(count):
        job = job_db.create_job(
            workflow_key=workspace_id,
            source_type="question",
            source_id=f"Q{i}",
            run_id=batch["id"],
            title=f"Q{i}",
            node_keys=_NODE_KEYS,
            workspace_id=workspace_id,
        )
        job_db.update_job_status(job["id"], "failed", "boom")
        ids.append(str(job["id"]))
    return ids


def _insert_material(job_db, workspace_id: str, material_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
            ),
        )


def _widen_start_item_types(job_db, workspace_id: str) -> None:
    """把 active revision 换成接受 material+ref 的同 DAG 变体（runs API 测试
    的 _accept_all_item_types 同一手法）。"""
    import copy

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "ref"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


# ---------------------------------------------------------------------------
# Submit channels: inline / multipart upload
# ---------------------------------------------------------------------------


def test_create_submit_inline_and_upload(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    base = f"/api/workspaces/{workspace_id}/campaigns"
    inline = client.post(
        base,
        json={
            "mode": "submit",
            "submit": {"items": [{"type": "material", "material_id": "mat-1"}]},
        },
    )
    assert inline.status_code == 200, inline.text
    assert inline.json()["campaign"]["target_spec"]["items"] == [
        {"type": "material", "material_id": "mat-1"}
    ]

    manifest = '{"type": "material", "material_id": "mat-1"}\n'
    upload = client.post(
        f"{base}/upload",
        files={"manifest": ("campaign.jsonl", manifest.encode("utf-8"), "application/x-ndjson")},
        data={"mode": "submit"},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["campaign"]["mode"] == "submit"
    assert upload.json()["campaign"]["status"] == "pending"


def test_upload_rejects_non_submit_mode(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    manifest = '{"type": "material", "material_id": "m"}\n'
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/upload",
        files={"manifest": ("campaign.jsonl", manifest.encode("utf-8"), "text/plain")},
        data={"mode": "rerun"},
    )
    assert response.status_code == 422


def test_upload_missing_manifest_422_from_fastapi(client, job_db) -> None:
    """四轮 P2（F3）：manifest 在 FastAPI 签名层必填——缺失 422 由框架
    校验产生（载荷点名字段），OpenAPI/生成的 api.ts 不再把它标 optional
    （类型安全客户端构造不出"合同合法却必失败"的请求）。"""
    workspace_id = _create_workspace(client, job_db)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/upload",
        data={"mode": "submit"},
    )
    assert response.status_code == 422, response.text
    assert any(error["loc"][-1] == "manifest" for error in response.json()["detail"]), response.text
    # 缺 manifest 的请求不建任何行（路由体根本不执行）。
    assert client.get(f"/api/workspaces/{workspace_id}/campaigns").json()["campaigns"] == []


def test_upload_over_limit_413_without_full_read(client, job_db, monkeypatch) -> None:
    """审核 P1：multipart 上传限读——最多读 manifest_max_bytes+1 字节，超限 413，
    不把整个超大请求体读进内存（`manifest.read(limit)` 的调用界就位）。"""
    workspace_id = _create_workspace(client, job_db)
    config = client.app.state.settings.executor_runtime.campaigns
    limit = config.manifest_max_bytes
    oversized = b"x" * (limit + 1)

    reads: list[int | None] = []
    original_read = _read_upload_size

    def _sized_read(upload, size=None):
        reads.append(size)
        return original_read(upload, size)

    monkeypatch.setattr("server.app.routes.campaigns._read_upload_size", _sized_read)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/upload",
        files={"manifest": ("big.jsonl", oversized, "application/x-ndjson")},
        data={"mode": "submit"},
    )
    assert response.status_code == 413, response.text
    # The single read is bounded by limit+1 — the whole 50MB+ body never
    # enters memory (the service-level len check would see exactly limit+1
    # and refuse anyway, but the route refuses without reading further).
    assert reads == [limit + 1]


def test_upload_at_exact_limit_reads_through(client, job_db, monkeypatch) -> None:
    """恰好 limit 字节：limit+1 的读界拿到全文，不误 413（错误是无效清单而非超限）。"""
    workspace_id = _create_workspace(client, job_db)
    config = client.app.state.settings.executor_runtime.campaigns
    payload = b"x" * config.manifest_max_bytes
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/upload",
        files={"manifest": ("edge.jsonl", payload, "application/x-ndjson")},
        data={"mode": "submit"},
    )
    # At the ceiling the bytes are accepted past the route bound; the
    # content then fails manifest parsing (not 'x' lines) — a 422
    # ManifestError (round-2 P2: file-manifest contract failures map 422),
    # NOT 413.
    assert response.status_code != 413
    assert response.status_code == 422


def test_create_json_items_count_ceiling(client, job_db, monkeypatch) -> None:
    """审核二轮 P1：JSON body 先读后限——items 数量在 Pydantic 契约层封顶
    （max_length），超限请求 422 于反序列化整个模型列表之前，而不是先构造
    5×10^5+ 个 RunItem 再由 service 的字节上限兜底。"""
    from pydantic import ValidationError

    from server.app.routes.campaign_contracts import (
        MAX_MANIFEST_ITEMS,
        CampaignCreateRequest,
    )

    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    # 不实际发 5×10^5 item 的 HTTP 请求体（测试进程自己就要吃这份内存）——
    # FastAPI 对 payload 的处理就是这个契约校验本身，直接钉同一入口。
    body = {
        "mode": "submit",
        "submit": {
            "items": [{"type": "material", "material_id": "mat-1"}] * (MAX_MANIFEST_ITEMS + 1)
        },
    }
    with pytest.raises(ValidationError, match="at most 500000 items"):
        CampaignCreateRequest.model_validate(body)
    # 合法数量不误拒（同形状、恰在上限内）。
    ok = CampaignCreateRequest.model_validate(
        {
            "mode": "submit",
            "submit": {"items": [{"type": "material", "material_id": "mat-1"}]},
        }
    )
    assert ok.submit is not None and len(ok.submit.items) == 1


# ---------------------------------------------------------------------------
# Round-2 fixes: start-node contract / RunItem contract on the upload
# channel / preview batch_size guard
# ---------------------------------------------------------------------------


def test_submit_ref_rejected_by_material_only_start_node(client, job_db) -> None:
    """审核二轮 P1：demo 入口只收 material——ref manifest 创建即 400，
    不建 pending campaign（同判定见 run_service.create_run 的
    validate_run_item_types）。"""
    workspace_id = _create_workspace(client, job_db)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    body = {
        "mode": "submit",
        "submit": {"items": [{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}]},
    }
    response = client.post(base, json=body)
    assert response.status_code == 400, response.text
    assert "not accepted" in response.json()["detail"]
    assert client.get(base).json()["campaigns"] == []


def test_submit_ref_accepted_by_widened_start_node(client, job_db) -> None:
    """合法值不误拒：入口契约收 ref 的 workspace，ref 创建成功（200）。"""
    workspace_id = _create_workspace(client, job_db)
    _widen_start_item_types(job_db, workspace_id)
    with job_db.connect() as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json, enabled)"
            " values ('cms', 'hmac_token', 'cms', '{}', 1)"
        )
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "mode": "submit",
            "submit": {"items": [{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}]},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["campaign"]["mode"] == "submit"


def test_upload_manifest_item_fails_runitem_contract(client, job_db) -> None:
    """审核二轮 P2：multipart 通道不再绕过 RunItem 合同——字符串 params /
    未知字段在 normalize 后被同一 discriminated 合同拒绝（422），不留存为
    job input。"""
    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    base = f"/api/workspaces/{workspace_id}/campaigns"

    bad_params = (
        '{"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": "oops"}\n'
    )
    response = client.post(
        f"{base}/upload",
        files={"manifest": ("m.jsonl", bad_params.encode("utf-8"), "application/x-ndjson")},
        data={"mode": "submit"},
    )
    assert response.status_code == 422, response.text
    assert "RunItem" in response.json()["detail"]

    bad_field = '{"type": "material", "material_id": "mat-1", "bogus": 1}\n'
    response = client.post(
        f"{base}/upload",
        files={"manifest": ("m.jsonl", bad_field.encode("utf-8"), "application/x-ndjson")},
        data={"mode": "submit"},
    )
    assert response.status_code == 422, response.text
    assert client.get(base).json()["campaigns"] == []


def test_upload_manifest_valid_items_not_overrejected(client, job_db) -> None:
    """合法值不误拒：合同内形状（material 直传、ref 带 dict params）照常 200，
    且落库的是合同化后的规范形（ref 补默认 params）。"""
    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    _widen_start_item_types(job_db, workspace_id)
    with job_db.connect() as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json, enabled)"
            " values ('cms', 'hmac_token', 'cms', '{}', 1)"
        )
    manifest = (
        '{"type": "material", "material_id": "mat-1"}\n'
        '{"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": {"k": "v"}}\n'
    )
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/upload",
        files={"manifest": ("m.jsonl", manifest.encode("utf-8"), "application/x-ndjson")},
        data={"mode": "submit"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["campaign"]["target_spec"]["items"]
    assert items[0] == {"type": "material", "material_id": "mat-1"}
    assert items[1] == {
        "type": "ref",
        "connection_key": "cms",
        "external_id": "Q-1",
        "params": {"k": "v"},
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_rerun_matches_legacy_preview_endpoint(client, job_db) -> None:
    """同函数即同数：campaign preview 与既有 batch-rerun preview 端点一致。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 5)
    legacy = client.post(
        f"/api/workspaces/{workspace_id}/jobs/batch-rerun/preview",
        json={"job_ids": ids, "node_key": _NODE_KEYS[0]},
    )
    assert legacy.status_code == 200, legacy.text
    via_campaign = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={
            "mode": "rerun",
            "rerun": {"job_ids": ids, "node_key": _NODE_KEYS[0]},
        },
    )
    assert via_campaign.status_code == 200, via_campaign.text
    legacy_body = legacy.json()
    campaign_body = via_campaign.json()["result"]
    assert campaign_body["mode"] == "rerun"
    assert campaign_body["total_count"] == legacy_body["total_count"] == 5
    assert campaign_body["eligible_count"] == legacy_body["eligible_count"]
    assert campaign_body["estimated_batches"] == 1
    # preview writes nothing
    assert client.get(f"/api/workspaces/{workspace_id}/campaigns").json()["campaigns"] == []


def test_preview_submit_counts(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, run_id, title,"
            " status, storage_dir, stem, created_at, updated_at)"
            " values ('job-existing', %s, 'material', 'mat-1', '', 'mat-1', 'completed',"
            " '', '', current_timestamp, current_timestamp)",
            (workspace_id,),
        )
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={
            "mode": "submit",
            "submit": {
                "items": [
                    {"type": "material", "material_id": "mat-1"},
                    {"type": "material", "material_id": "mat-1"},
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["mode"] == "submit"
    assert body["total_items"] == 2
    assert body["would_create"] == 0
    assert body["would_skip"] == 2


def test_preview_rejects_over_ceiling_batch_size(client, job_db) -> None:
    """审核二轮 P2：preview 的 batch_size 护栏与创建一致——超
    rerun_max_batch_size 的 dry-run 400，不再确认一个无法创建的 campaign。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    config = client.app.state.settings.executor_runtime.campaigns
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={
            "mode": "rerun",
            "rerun": {
                "job_ids": ids,
                "node_key": _NODE_KEYS[0],
                "batch_size": config.rerun_max_batch_size + 1,
            },
        },
    )
    assert response.status_code == 400, response.text
    assert "rerun_max_batch_size" in response.json()["detail"]


def test_preview_batch_size_at_ceiling_ok(client, job_db) -> None:
    """合法值不误拒：恰在上限的 batch_size，preview 200。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    config = client.app.state.settings.executor_runtime.campaigns
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={
            "mode": "rerun",
            "rerun": {
                "job_ids": ids,
                "node_key": _NODE_KEYS[0],
                "batch_size": config.rerun_max_batch_size,
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["batch_size"] == config.rerun_max_batch_size


# ---------------------------------------------------------------------------
# Preview: upgrade mode
# ---------------------------------------------------------------------------


def test_preview_upgrade_counts_eligible(client, job_db) -> None:
    """审核 P2：upgrade preview 用升级写路径的资格判定（非 current 即可升级），
    不再走 rerun preview 的 node_key 判定（那里对 upgrade 选集恒 0）。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 3)
    # _seed_failed_jobs 建的 job 不带 revision 快照（stale）——全部 eligible。
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={"mode": "upgrade", "rerun": {"job_ids": ids}},
    )
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["mode"] == "upgrade"
    assert body["total_count"] == 3
    assert body["eligible_count"] == 3
    assert body["eligible_count"] > 0  # the P2 regression pin
    # preview writes nothing
    assert client.get(f"/api/workspaces/{workspace_id}/campaigns").json()["campaigns"] == []


def test_preview_upgrade_marks_current_jobs_ineligible(client, job_db) -> None:
    """与 upgrade 写路径同判定：pin+snapshot 都等于 active revision 的 job 跳过。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    active = job_db.get_active_workflow_revision(workspace_id, workspace_id)
    assert active is not None
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set workflow_revision_id=%s, workflow_definition_snapshot_json=%s"
            " where id=%s",
            (str(active["id"]), str(active["definition_json"]), ids[0]),
        )
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={"mode": "upgrade", "rerun": {"job_ids": ids}},
    )
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["total_count"] == 2
    assert body["eligible_count"] == 1


def test_preview_upgrade_matches_batch_upgrade_write_path(client, job_db) -> None:
    """preview 与真实路径共享判定：upgrade campaign preview 的 eligible 数
    与 batch-upgrade-workflow 端点实际升级的 succeeded+failed 数一致
    （busy 类 skip 不影响本种子——无活跃 lease）。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 3)
    preview = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={"mode": "upgrade", "rerun": {"job_ids": ids}},
    )
    assert preview.status_code == 200, preview.text
    eligible = preview.json()["result"]["eligible_count"]

    results = client.post(
        f"/api/workspaces/{workspace_id}/jobs/batch-upgrade-workflow",
        json={"job_ids": ids},
    )
    assert results.status_code == 200, results.text
    statuses = [r["status"] for r in results.json()["results"]]
    # The write path treats every eligible job as a real attempt (succeeded
    # or failed), so attempts == preview's eligible_count.
    assert len(statuses) == eligible
    assert all(status in ("succeeded", "failed", "skipped") for status in statuses)


def test_preview_upgrade_filter_form(client, job_db) -> None:
    """filter 形态的 upgrade 选集同样走升级判定（不再恒 0）。"""
    workspace_id = _create_workspace(client, job_db)
    _seed_failed_jobs(client, job_db, workspace_id, 2)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={"mode": "upgrade", "rerun": {"filter": {"status": "failed"}}},
    )
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["total_count"] == 2
    assert body["eligible_count"] == 2


# ---------------------------------------------------------------------------
# Wiring: the service must receive the ObjectStorage client, not the wrapper
# ---------------------------------------------------------------------------


def test_campaign_service_gets_object_storage_client(client, job_db) -> None:
    """审核 P1：main.py 组装传入 deps.job_artifact_objects 是
    JobArtifactObjectStore 包装器；service 需要底层 .storage（put_object 所在）。
    组装后 service.object_storage 必须是同一 storage 本体（或 None——未配置
    S3 的实例走 503 分支）。"""
    from server.app.routes.campaign_wiring import build_campaign_service
    from server.app.routes.deps import RouterDeps
    from server.app.services.job_artifact_objects import JobArtifactObjectStore
    from tests.fakes.storage import FakeObjectStorage

    app = client.app
    wired = app.state.job_artifact_objects
    # The app's wrapper holds whatever build_s3_storage_checked produced.
    assert isinstance(wired, JobArtifactObjectStore)
    underlying = wired.storage
    assert underlying is None or isinstance(underlying, FakeObjectStorage)

    def _build(wrapper_storage):
        deps = RouterDeps(
            job_db=app.state.job_db,
            settings=app.state.settings,
            agent_manager=app.state.agent_manager,
            agent_catalog=None,
            workspace_execution_configuration=None,
            workspace_configuration=None,
            job_packages=None,
            job_artifact_objects=JobArtifactObjectStore(job_db, wrapper_storage),
        )
        return build_campaign_service(deps)

    # The exact seam the manifest spill path calls: put_object lives on the
    # underlying ObjectStorage, never on the wrapper.
    fake = FakeObjectStorage()
    service = _build(fake)
    assert service.object_storage is fake
    assert hasattr(service.object_storage, "put_object")
    # Unconfigured instance: None stays None (the 503 branch), never the
    # truthy wrapper (which would AttributeError on put_object).
    assert _build(None).object_storage is None


def test_app_state_wiring_passes_wrapper_but_service_unwraps(client) -> None:
    """main.py 的 RouterDeps 仍带包装器（agent worker 面也用它）；campaign
    组装处的解包是唯一的修复面——service 拿到的绝不能是包装器。"""
    from server.app.services.job_artifact_objects import JobArtifactObjectStore

    app = client.app
    wrapper = app.state.job_artifact_objects
    assert isinstance(wrapper, JobArtifactObjectStore)
    # The wrapper itself has no put_object — the bug shape the wiring must
    # not pass through.
    assert not hasattr(wrapper, "put_object")


# ---------------------------------------------------------------------------
# Round-3 fixes: per-item field lengths / id-list ceiling / JSON body byte
# limit / manifest key prefix
# ---------------------------------------------------------------------------


def test_oversized_item_field_422(client, job_db) -> None:
    """三轮 P1：单 item 的字段级长度上限——512 之外的 ID 字符串、超 64KB 的
    params 在契约层 422（请求体的 OOM 面收口到 ASGI 之前）。"""
    workspace_id = _create_workspace(client, job_db)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    response = client.post(
        base,
        json={
            "mode": "submit",
            "submit": {"items": [{"type": "material", "material_id": "m" * 513}]},
        },
    )
    assert response.status_code == 422, response.text
    assert client.get(base).json()["campaigns"] == []


def test_oversized_item_params_422(client, job_db) -> None:
    """三轮 P1：单 item 的 params 序列化上限（64KB）——单个超大字符串 params
    422，不再等 service 的字节上限兜底。"""
    workspace_id = _create_workspace(client, job_db)
    _widen_start_item_types(job_db, workspace_id)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "mode": "submit",
            "submit": {
                "items": [
                    {
                        "type": "ref",
                        "connection_key": "cms",
                        "external_id": "Q-1",
                        "params": {"blob": "x" * 70_000},
                    }
                ]
            },
        },
    )
    assert response.status_code == 422, response.text


def test_item_fields_at_limits_not_overrejected(client, job_db) -> None:
    """合法值不误拒：恰在 512 的 ID、接近 64KB 的 params 照常过契约层。"""
    from pydantic import ValidationError

    from server.app.routes.run_contracts import RunItemRef

    ok = RunItemRef.model_validate(
        {
            "type": "ref",
            "connection_key": "c" * 512,
            "external_id": "Q" * 512,
            "params": {"k": "v" * 65_000},
        }
    )
    assert len(ok.connection_key) == 512
    assert len(ok.external_id) == 512
    with pytest.raises(ValidationError):
        RunItemRef.model_validate(
            {
                "type": "ref",
                "connection_key": "c" * 513,
                "external_id": "Q-1",
                "params": {},
            }
        )


def test_job_ids_selection_ceiling_422(client, job_db) -> None:
    """三轮 P1：rerun 的 job_ids 列表在契约层封顶（100,000）——超限 422，
    不再进入 service 解析。不实际发 10^5+ 个 id 的 HTTP 请求体——
    FastAPI 对 payload 的处理就是这个契约校验本身，直接钉同一入口。"""
    from pydantic import ValidationError

    from server.app.routes.campaign_contracts import (
        MAX_JOB_ID_SELECTION,
        CampaignCreateRequest,
    )

    body = {
        "mode": "rerun",
        "rerun": {"job_ids": ["j"] * (MAX_JOB_ID_SELECTION + 1), "node_key": "n"},
    }
    with pytest.raises(ValidationError, match="at most 100000 items"):
        CampaignCreateRequest.model_validate(body)
    # 合法形状不误拒。
    ok = CampaignCreateRequest.model_validate(
        {"mode": "rerun", "rerun": {"job_ids": ["j1", "j2"], "node_key": "n"}}
    )
    assert ok.rerun is not None and ok.rerun.job_ids == ["j1", "j2"]


def test_json_body_over_byte_limit_413(client, job_db, monkeypatch) -> None:
    """三轮 P1：JSON 通道的 ASGI 字节上限——单 item 携带超限 body 时 413，
    FastAPI 不再完整读入并构造模型列表。上限 = manifest_max_bytes × 2（手写
    JSON 比规范 jsonl 更宽裕的 headroom），用 monkeypatch 压小让用例不搬
    50MB 进内存；正文走真实 HTTP 面（TestClient 的 content-length 路径）。"""
    from server.app.routes.campaign_body_limit import campaign_body_limit_max_bytes

    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    config = client.app.state.settings.executor_runtime.campaigns
    # 压小 manifest_max_bytes → body 上限 = 2×64B = 128B；一个 ~200B 的合法
    # 形状 body 超限（但远小于任何内存压力——测试只钉判定，不制造 OOM）。
    monkeypatch.setattr(config, "manifest_max_bytes", 64)
    assert campaign_body_limit_max_bytes(client.app.state.settings) == 128
    body = {
        "mode": "submit",
        "submit": {"items": [{"type": "material", "material_id": "m" * 150}]},
    }
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json=body,
    )
    assert response.status_code == 413, response.text
    assert "exceeds" in response.json()["detail"]


def test_json_body_within_byte_limit_not_overrejected(client, job_db, monkeypatch) -> None:
    """合法值不误拒：body 在上限内（含字段子上限之内）照常 200。"""
    from server.app.routes.campaign_body_limit import campaign_body_limit_max_bytes

    workspace_id = _create_workspace(client, job_db)
    _insert_material(job_db, workspace_id, "mat-1")
    config = client.app.state.settings.executor_runtime.campaigns
    monkeypatch.setattr(config, "manifest_max_bytes", 256)
    assert campaign_body_limit_max_bytes(client.app.state.settings) == 512
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "mode": "submit",
            "submit": {"items": [{"type": "material", "material_id": "mat-1"}]},
        },
    )
    assert response.status_code == 200, response.text


# The ASGI middleware's own unit tests (chunked oversize / ambiguous
# content-length / path scoping) live in tests/routes/test_campaign_body_limit.py.
