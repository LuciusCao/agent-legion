"""Campaigns API contract tests (#532 PR-A).

Pins the endpoint surface (create JSON / list / detail / pause / resume /
cancel), the permission matrix (viewer 403 on writes but 200 on reads,
non-member 404 — anti-enumeration, studio-agent token refused on every
route of the group), and the PR-A intermediate state: a created campaign
stays ``pending`` (no feeder in this slice) and is fully visible over the
API. The submit manifest channels (upload/inline/RunItem contract/size
gates) and the preview endpoints live in the sibling file
tests/routes/test_campaigns_api_upload_preview.py (PR #541 round-3 P1
split, zero-churn migration).
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


def _create_member(client, username="campaign-member", password="pw1") -> str:
    response = client.post(
        "/api/users", json={"username": username, "password": password}, headers=CSRF
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _member_client(client, username="campaign-member", password="pw1"):
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _rerun_body(ids: list[str], **knobs) -> dict:
    body = {"mode": "rerun", "rerun": {"job_ids": ids, "node_key": _NODE_KEYS[0]}}
    body["rerun"].update(knobs)
    return body


# ---------------------------------------------------------------------------
# Contract: create / list / detail
# ---------------------------------------------------------------------------


def test_campaigns_require_auth(anon_client) -> None:
    url = "/api/workspaces/ws-1/campaigns"
    assert anon_client.get(url).status_code == 401
    assert anon_client.post(url, json={}).status_code == 401


def test_create_rerun_campaign_stays_pending(client, job_db) -> None:
    """PR-A 中间态：无 feeder，创建的 campaign 停在 pending、API 可见。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 3)
    response = client.post(f"/api/workspaces/{workspace_id}/campaigns", json=_rerun_body(ids))
    assert response.status_code == 200, response.text
    body = response.json()["campaign"]
    assert body["mode"] == "rerun"
    assert body["status"] == "pending"
    assert body["watermark"] == 30_000
    assert body["batch_size"] == 5_000
    assert body["target_spec"]["job_ids"] == sorted(ids)
    assert body["created_by"] != ""  # the session user id flows through

    listed = client.get(f"/api/workspaces/{workspace_id}/campaigns").json()["campaigns"]
    assert [c["id"] for c in listed] == [body["id"]]

    detail = client.get(f"/api/workspaces/{workspace_id}/campaigns/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["campaign"]["status"] == "pending"


def test_create_with_knob_overrides(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json=_rerun_body(ids, watermark=100, batch_size=50),
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["watermark"] == 100
    assert campaign["batch_size"] == 50


def test_create_from_filter_form(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    _seed_failed_jobs(client, job_db, workspace_id, 4)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "mode": "rerun",
            "rerun": {"filter": {"status": "failed"}, "node_key": _NODE_KEYS[0]},
        },
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["target_spec"]["filter"]["status"] == "failed"
    # filter 形态不物化 ids 快照（设计 §1.4 的行宽护栏）。
    assert "job_ids" not in campaign["target_spec"]


def test_create_with_name_roundtrip(client, job_db) -> None:
    """PR-D「任务名称」：create 请求的 name 进 target_spec 并以顶层字段
    回读（列表显示用）；缺省时 name 为空串（前端派生默认名）。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={**_rerun_body(ids), "name": "重跑 · 全部失败任务"},
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["name"] == "重跑 · 全部失败任务"
    assert campaign["target_spec"]["name"] == "重跑 · 全部失败任务"

    # 缺省 name：campaign_record 的顶层 name 为空串（默认名是 UI 派生）。
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json=_rerun_body(ids),
    )
    assert response.status_code == 200, response.text
    assert response.json()["campaign"]["name"] == ""


def test_create_upgrade_mode(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={"mode": "upgrade", "rerun": {"job_ids": ids}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["campaign"]["mode"] == "upgrade"


def test_create_from_filter_form_with_exclude_ids(client, job_db) -> None:
    """P2-1：filter + exclude_ids（allMatching 反选）进 target_spec；显式
    ids 形态忽略 exclude_ids（不落键）。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 3)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    response = client.post(
        base,
        json={
            "mode": "rerun",
            "rerun": {
                "filter": {"status": "failed"},
                "exclude_ids": [ids[0]],
                "node_key": _NODE_KEYS[0],
            },
        },
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["target_spec"]["exclude_ids"] == [ids[0]]
    assert campaign["target_spec"]["filter"]["status"] == "failed"

    # 显式 ids 形态：exclude_ids 不生效也不进 spec。
    response = client.post(
        base,
        json={
            "mode": "rerun",
            "rerun": {
                "job_ids": ids,
                "exclude_ids": [ids[0]],
                "node_key": _NODE_KEYS[0],
            },
        },
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["target_spec"]["job_ids"] == sorted(ids)
    assert "exclude_ids" not in campaign["target_spec"]


def test_create_validation_4xx(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    # pydantic: node_key required without from_failed_node
    assert client.post(base, json={"mode": "rerun", "rerun": {"job_ids": ids}}).status_code == 422
    # pydantic: exactly one of job_ids / filter
    assert (
        client.post(
            base,
            json={
                "mode": "rerun",
                "rerun": {
                    "job_ids": ids,
                    "filter": {"status": "failed"},
                    "node_key": _NODE_KEYS[0],
                },
            },
        ).status_code
        == 422
    )
    # service: unknown mode is a 400 via the contract's Literal → 422
    assert client.post(base, json={"mode": "bogus", "rerun": {"job_ids": ids}}).status_code == 422
    # service: empty selection fail-fast
    assert (
        client.post(
            base,
            json={
                "mode": "rerun",
                "rerun": {"filter": {"status": "completed"}, "node_key": _NODE_KEYS[0]},
            },
        ).status_code
        == 400
    )


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
        data={"mode": "submit", "name": "添加 · 开学季补录"},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["campaign"]["mode"] == "submit"
    assert upload.json()["campaign"]["status"] == "pending"
    assert upload.json()["campaign"]["name"] == "添加 · 开学季补录"


def test_upload_rejects_non_submit_mode(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    manifest = '{"type": "material", "material_id": "m"}\n'
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/upload",
        files={"manifest": ("campaign.jsonl", manifest.encode("utf-8"), "text/plain")},
        data={"mode": "rerun"},
    )
    assert response.status_code == 422


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
    # content then fails manifest parsing (not 'x' lines) — a 400-range
    # error, NOT 413.
    assert response.status_code != 413
    assert response.status_code == 400


def test_unknown_campaign_404(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    assert client.get(f"{base}/nope").status_code == 404
    assert client.post(f"{base}/nope/pause").status_code == 404
    assert client.post(f"{base}/nope/resume").status_code == 404
    assert client.post(f"{base}/nope/cancel").status_code == 404


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


def test_preview_rerun_filter_form_honors_exclude_ids(client, job_db) -> None:
    """P2-1 preview 口径：campaign preview 透传 exclude_ids，计数与旧同步
    路径（filter + exclude_ids 载荷）一致——对话框试算数不漂移。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 5)
    legacy = client.post(
        f"/api/workspaces/{workspace_id}/jobs/batch-rerun/preview",
        json={
            "filter": {"status": "failed"},
            "exclude_ids": ids[4:],
            "node_key": _NODE_KEYS[0],
        },
    )
    assert legacy.status_code == 200, legacy.text
    via_campaign = client.post(
        f"/api/workspaces/{workspace_id}/campaigns/preview",
        json={
            "mode": "rerun",
            "rerun": {
                "filter": {"status": "failed"},
                "exclude_ids": ids[4:],
                "node_key": _NODE_KEYS[0],
            },
        },
    )
    assert via_campaign.status_code == 200, via_campaign.text
    campaign_body = via_campaign.json()["result"]
    legacy_body = legacy.json()
    assert campaign_body["total_count"] == legacy_body["total_count"] == 4
    assert campaign_body["eligible_count"] == legacy_body["eligible_count"]


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
# Submit mode end-to-end (PR-C): create → feeder → linked runs → detail
# ---------------------------------------------------------------------------


def test_submit_campaign_end_to_end_with_detail_runs(client, job_db) -> None:
    """PR-C 全链路（API 面）：inline submit 创建 → app.state.campaign_feeder 的
    tick 投放（start_worker=False 的测试 app 不启线程，tick 手动驱动）→
    runs.campaign_id 落写 → 详情端点聚合 run 概览（CampaignDetailRecord.runs）。
    rerun 模式的详情不带 runs 聚合（空列表）。"""
    workspace_id = _create_workspace(client, job_db)
    for i in range(4):
        _insert_material(job_db, workspace_id, f"mat-e2e-{i}")
    base = f"/api/workspaces/{workspace_id}/campaigns"

    created = client.post(
        base,
        json={
            "mode": "submit",
            "submit": {
                "items": [{"type": "material", "material_id": f"mat-e2e-{i}"} for i in range(4)],
                "batch_size": 2,
                "watermark": 100,
            },
        },
    )
    assert created.status_code == 200, created.text
    campaign_id = created.json()["campaign"]["id"]
    assert created.json()["campaign"]["progress"] == {"item_offset": 0}

    feeder = client.app.state.campaign_feeder
    assert feeder is not None  # built in create_app; only the thread is off
    # The shared test app's workspaces start paused (the startup
    # reset_all_to_paused discipline): the feeder correctly suspends a
    # paused workspace (design §2.4), so resume it first — exactly what the
    # operator's workspace-resume flow does for a stalled campaign.
    feeder.workspace_worker_control.resume(workspace_id)
    feeder._tick()
    feeder._next_feed_at.clear()
    feeder._tick()

    detail = client.get(f"{base}/{campaign_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()["campaign"]
    assert body["status"] == "completed"
    assert body["batches_submitted"] == 2
    assert body["jobs_succeeded"] == 4
    assert body["progress"]["item_offset"] == 4
    runs = body["runs"]
    assert len(runs) == 2
    assert sorted(run["job_count"] for run in runs) == [2, 2]
    with job_db.connect() as conn:
        linked = conn.execute(
            "select campaign_id from runs where campaign_id=%s", (campaign_id,)
        ).fetchall()
    assert len(linked) == 2

    # Rerun-mode detail carries no runs aggregate.
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    rerun_id = client.post(base, json=_rerun_body(ids)).json()["campaign"]["id"]
    rerun_detail = client.get(f"{base}/{rerun_id}")
    assert rerun_detail.status_code == 200
    assert rerun_detail.json()["campaign"]["runs"] == []


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


def test_pause_resume_cancel_cycle(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    campaign_id = client.post(base, json=_rerun_body(ids)).json()["campaign"]["id"]

    paused = client.post(f"{base}/{campaign_id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["campaign"]["status"] == "paused"

    resumed = client.post(f"{base}/{campaign_id}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["campaign"]["status"] == "running"

    # Double resume is a 409.
    assert client.post(f"{base}/{campaign_id}/resume").status_code == 409

    cancelled = client.post(f"{base}/{campaign_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["campaign"]["status"] == "cancelled"

    # Terminal: every further transition is a 409.
    assert client.post(f"{base}/{campaign_id}/cancel").status_code == 409
    assert client.post(f"{base}/{campaign_id}/pause").status_code == 409
    assert client.post(f"{base}/{campaign_id}/resume").status_code == 409


# ---------------------------------------------------------------------------
# Permission matrix
# ---------------------------------------------------------------------------


def test_viewer_reads_but_cannot_write(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    created = client.post(base, json=_rerun_body(ids))
    assert created.status_code == 200
    campaign_id = created.json()["campaign"]["id"]

    member_id = _create_member(client)
    job_db.upsert_workspace_member(workspace_id, member_id, "viewer")
    viewer = _member_client(client)

    # Reads pass (SAFE methods carry viewer read rights).
    assert viewer.get(base).status_code == 200
    assert viewer.get(f"{base}/{campaign_id}").status_code == 200

    # Preview is a POST (non-SAFE) and carries the editor gate like every
    # write-shaped route; it writes nothing, but the access layer's SAFE/
    # non-SAFE split is the uniform rule.
    assert (
        viewer.post(
            base + "/preview",
            json={"mode": "rerun", "rerun": {"job_ids": ids, "node_key": _NODE_KEYS[0]}},
        ).status_code
        == 403
    )

    # Every write is 403.
    assert viewer.post(base, json=_rerun_body(ids)).status_code == 403
    assert viewer.post(f"{base}/{campaign_id}/pause").status_code == 403
    assert viewer.post(f"{base}/{campaign_id}/resume").status_code == 403
    assert viewer.post(f"{base}/{campaign_id}/cancel").status_code == 403
    upload = viewer.post(
        f"{base}/upload",
        files={"manifest": ("m.jsonl", b"{}", "text/plain")},
        data={"mode": "submit"},
    )
    assert upload.status_code == 403


def test_non_member_gets_404(client, job_db) -> None:
    """防枚举：非成员读也 404（不是 403）。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    campaign_id = client.post(base, json=_rerun_body(ids)).json()["campaign"]["id"]

    _create_member(client)
    member = _member_client(client)
    assert member.get(base).status_code == 404
    assert member.get(f"{base}/{campaign_id}").status_code == 404
    assert member.post(base, json=_rerun_body(ids)).status_code == 404
    assert member.post(f"{base}/{campaign_id}/pause").status_code == 404


def test_editor_can_write(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    member_id = _create_member(client, username="campaign-editor")
    job_db.upsert_workspace_member(workspace_id, member_id, "editor")
    editor = _member_client(client, username="campaign-editor")

    created = editor.post(base, json=_rerun_body(ids))
    assert created.status_code == 200, created.text
    campaign_id = created.json()["campaign"]["id"]
    assert editor.post(f"{base}/{campaign_id}/pause").status_code == 200


def test_studio_agent_scope_refused_on_every_route(client, job_db) -> None:
    """STUDIO-AGENT-001：campaign 全部路由拒 studio-agent token（写面尤甚）。"""
    from server.app.auth import scoped_tokens

    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    created = client.post(base, json=_rerun_body(ids))
    assert created.status_code == 200
    campaign_id = created.json()["campaign"]["id"]

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(
        job_db, admin_id, scope="studio_agent", workspace_id=workspace_id
    )
    agent = client.__class__(client.app)
    agent.headers["authorization"] = f"Bearer {token}"

    def _call(method: str, path: str, **kwargs):
        response = getattr(agent, method)(path, **kwargs)
        return response.status_code

    assert _call("get", base) == 403
    assert _call("get", f"{base}/{campaign_id}") == 403
    assert _call("post", base, json=_rerun_body(ids)) == 403
    assert _call("post", f"{base}/{campaign_id}/pause") == 403
    assert _call("post", f"{base}/{campaign_id}/resume") == 403
    assert _call("post", f"{base}/{campaign_id}/cancel") == 403
