"""CampaignService: submit target + preview judgements at the service layer.

Split out of tests/services/test_campaign_service.py (PR #541 round-3 P1:
the single file passed the 800-line split threshold). Covers the submit
manifest lifecycle (normalization channels, intake preflight, storage
decision, quota-before-PUT) and the preview dry-run judgements (rerun /
upgrade / submit), including the round-3 review pins: per-run item ceiling
applied per batch (not per manifest), the serialized-bytes check shared by
preview and create, the advisory quota precheck ahead of the bucket PUT,
and the GC-isolated manifest key prefix.
"""

from __future__ import annotations

import pytest

from server.app.services.campaign_manifest import ManifestError
from server.app.services.campaign_service import (
    CAMPAIGN_MANIFEST_KEY_PREFIX,
    CampaignManifestTooLargeError,
    CampaignService,
    CampaignStorageUnavailableError,
    campaign_manifest_key,
)
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from tests.helpers import publish_builtin_revision

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]


class FakeObjectStorage:
    """ObjectStorage test double: put_object captures, open_stream replays."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        self.puts.append(storage_key)
        self.objects[storage_key] = data

    def open_stream(self, storage_key: str):
        import io

        return io.BytesIO(self.objects[storage_key])


def _seed_workspace_with_revision(job_db, workspace_id: str) -> str:
    workspace = job_db.create_workspace(workspace_id, default_workflow_key=workspace_id)
    publish_builtin_revision(job_db, str(workspace["id"]))
    return str(workspace["id"])


def _seed_failed_jobs(job_db, workspace_id: str, count: int) -> list[str]:
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


def _insert_connection(job_db, key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json, enabled)"
            " values (%s, 'hmac_token', %s, '{}', 1)",
            (key, key),
        )


def _insert_job(job_db, workspace_id: str, source_type: str, source_id: str) -> str:
    job = job_db.create_job(
        workflow_key=workspace_id,
        source_type=source_type,
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=_NODE_KEYS,
        workspace_id=workspace_id,
    )
    return str(job["id"])


def _widen_start_item_types(job_db, workspace_id: str) -> None:
    """把 workspace 的 active revision 换成接受 material+ref 的同 DAG 变体。

    播种的 demo revision 只收 material（EXEC-WORKFLOW-START-001）；带 ref
    item 的 submit 用例先发布该变体（与 tests/routes/test_runs_api.py 的
    _accept_all_item_types 同一手法——发布侧改入口契约，不改判定链）。
    """
    import copy

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "ref"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


@pytest.fixture
def campaign_service(job_db, settings):
    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.services.job_rerun import JobRerunService

    rerun = JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
    )
    return CampaignService(job_db, settings, rerun_service=rerun)


# ---------------------------------------------------------------------------
# Create: submit target
# ---------------------------------------------------------------------------


class TestCreateSubmitTarget:
    def test_inline_items_fail_fast_on_unknown_material(self, campaign_service, job_db):
        """创建即全量判定：item 解析失败 → 不建行（fail-fast）。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-submit-ws")
        with pytest.raises(NotFoundError, match="Material not found"):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                items=[{"type": "material", "material_id": "nope"}],
            )
        assert campaign_service.list_campaigns(workspace_id) == []

    def test_inline_items_within_ceiling_stored_in_spec(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-inline-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        row = campaign_service.create_campaign(
            workspace_id,
            "submit",
            items=[{"type": "material", "material_id": "mat-1"}],
        )
        assert row["target_spec"]["items"] == [{"type": "material", "material_id": "mat-1"}]

    def test_manifest_upload_bytes_normalized(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-upload-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        _insert_material(job_db, workspace_id, "mat-2")
        manifest = (
            '{"type": "material", "material_id": "mat-1"}\n'
            "# comment\n"
            '{"type": "material", "material_id": "mat-2"}\n'
        )
        row = campaign_service.create_campaign(
            workspace_id,
            "submit",
            manifest_filename="campaign.jsonl",
            manifest_bytes=manifest.encode("utf-8"),
        )
        assert row["target_spec"]["items"] == [
            {"type": "material", "material_id": "mat-1"},
            {"type": "material", "material_id": "mat-2"},
        ]

    def test_manifest_csv_mixed_header(self, campaign_service, job_db):
        """CSV 混合表头空列丢弃（#531 P2-2）的服务端创建路径。

        入口契约收 material+ref（发布变体），ref 行才过创建时的
        start-node preflight（二轮 P1）。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-csv-ws")
        _widen_start_item_types(job_db, workspace_id)
        _insert_material(job_db, workspace_id, "m-1")
        _insert_connection(job_db, "cms-main")
        csv_bytes = (
            b"type,material_id,bundle_id,connection_key,external_id\n"
            b"material,m-1,,,\n"
            b"ref,,,cms-main,Q-1\n"
        )
        row = campaign_service.create_campaign(
            workspace_id, "submit", manifest_filename="campaign.csv", manifest_bytes=csv_bytes
        )
        assert row["target_spec"]["items"] == [
            {"type": "material", "material_id": "m-1"},
            {"type": "ref", "connection_key": "cms-main", "external_id": "Q-1", "params": {}},
        ]

    def test_invalid_manifest_rejected_without_row(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-bad-manifest-ws")
        # 二轮 P2：文件清单的合同失败按 ManifestError（路由 422）穿透，
        # 不再在 service 层降级为 400 形 InvalidOperationError。
        with pytest.raises(ManifestError, match="不支持的 item type"):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                manifest_filename="campaign.jsonl",
                manifest_bytes=b'{"type": "video", "id": "x"}\n',
            )
        assert campaign_service.list_campaigns(workspace_id) == []

    def test_manifest_over_max_bytes_rejected(self, campaign_service, job_db, settings):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-big-ws")
        limit = settings.executor_runtime.campaigns.manifest_max_bytes
        with pytest.raises(CampaignManifestTooLargeError):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                manifest_filename="big.jsonl",
                manifest_bytes=b"x" * (limit + 1),
            )

    def test_json_items_over_max_bytes_rejected_too(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """审核 P2-1：大小上限必须覆盖 JSON inline-items→bucket 通道——
        multipart 有 413 检查，JSON items 序列化超上限不能静默绕过进桶。"""
        config = settings.executor_runtime.campaigns
        monkeypatch.setattr(config, "manifest_inline_max_bytes", 64)
        monkeypatch.setattr(config, "manifest_max_bytes", 100)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-json-big-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        storage = FakeObjectStorage()
        campaign_service.object_storage = storage
        with pytest.raises(CampaignManifestTooLargeError):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                # 每个 item 45B：3 个共 135B——超 100B 上限、超 64B 行内上限
                # （必然走存储决策分支——正是在该分支前补的上限检查被钉住）。
                items=[{"type": "material", "material_id": "mat-1"}] * 3,
            )
        assert storage.objects == {}, "no bucket write may happen past the cap"

    def test_over_inline_ceiling_requires_storage(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """超过行内上限：无对象存储的实例 503（CampaignStorageUnavailableError）；
        有存储则落桶、spec 只存 key + item 数。上限压小让用例不依赖大清单。"""
        config = settings.executor_runtime.campaigns
        monkeypatch.setattr(config, "manifest_inline_max_bytes", 64)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-bucket-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        items = [
            {"type": "material", "material_id": "mat-1"},
            {"type": "material", "material_id": "mat-1"},
        ]
        # 全量 item 判定先于存储决策：无存储的实例对超行内清单 503。
        with pytest.raises(CampaignStorageUnavailableError):
            campaign_service.create_campaign(workspace_id, "submit", items=items)

        storage = FakeObjectStorage()
        campaign_service.object_storage = storage
        # 解析失败仍然 fail-fast 在建行之前（落桶也拦不住坏 item）。
        with pytest.raises(NotFoundError):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                items=[
                    {"type": "material", "material_id": "mat-1"},
                    {"type": "material", "material_id": "m-2"},
                ],
            )
        row = campaign_service.create_campaign(workspace_id, "submit", items=items)
        assert "items" not in row["target_spec"]
        key = row["target_spec"]["manifest_storage_key"]
        assert key == campaign_manifest_key(workspace_id, row["id"])
        assert row["target_spec"]["manifest_item_count"] == 2
        assert key in storage.objects
        # 落桶的是规范化 jsonl，feeder 可原样 parse_manifest_text 回放。
        from server.app.services.campaign_manifest import parse_manifest_text

        replayed = parse_manifest_text(storage.objects[key].decode("utf-8"))
        assert replayed == items

    def test_submit_needs_items_or_manifest(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-none-ws")
        with pytest.raises(InvalidOperationError, match="items or a manifest"):
            campaign_service.create_campaign(workspace_id, "submit")

    def test_submit_item_type_not_accepted_by_start_node(self, campaign_service, job_db):
        """审核二轮 P1：start node 只收 material 时，ref manifest 在创建时
        即被拒（同一判定 feeder 的 create_run 会用——validate_run_item_types），
        不能先建 pending campaign、投放时才炸。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-start-contract-ws")
        _insert_connection(job_db, "cms-main")
        # 播种的 demo revision：_start.accepted_item_types == ["material"]。
        with pytest.raises(InvalidOperationError, match="not accepted by this workflow"):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                items=[{"type": "ref", "connection_key": "cms-main", "external_id": "Q-1"}],
            )
        # multipart 通道同判定（normalize 后同一 preflight）。
        with pytest.raises(InvalidOperationError, match="not accepted by this workflow"):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                manifest_filename="m.jsonl",
                manifest_bytes=b'{"type": "ref", "connection_key": "cms-main", "external_id": "Q-1"}\n',
            )
        # 全量判定在建行之前：零行、零桶写。
        assert campaign_service.list_campaigns(workspace_id) == []

    def test_submit_preview_rejects_item_type_not_accepted(self, campaign_service, job_db):
        """preview 与创建共享判定：入口契约不收的 item 不计 would_create，
        dry-run 直接报创建时的同一错误。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-preview-contract-ws")
        _insert_connection(job_db, "cms-main")
        with pytest.raises(InvalidOperationError, match="not accepted by this workflow"):
            campaign_service.preview_campaign(
                workspace_id,
                "submit",
                items=[{"type": "ref", "connection_key": "cms-main", "external_id": "Q-1"}],
            )

    def test_submit_without_active_revision_rejected(self, campaign_service, job_db):
        """审核二轮 P1：无 active revision 的 workspace，create_run 会拒每一个
        item——submit campaign 创建时同样 fail-fast（不建 pending 行）。"""
        workspace = job_db.create_workspace(
            "campaign-no-active-revision-ws",
            default_workflow_key="campaign-no-active-revision-ws",
        )
        workspace_id = str(workspace["id"])
        with pytest.raises(InvalidOperationError, match="no active workflow revision"):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                items=[{"type": "material", "material_id": "mat-x"}],
            )
        assert campaign_service.list_campaigns(workspace_id) == []

    def test_submit_manifest_over_per_run_limit_allowed_in_batches(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """三轮 P1 语义修正：workflows.max_items_per_run 约束的是 feeder 的
        单次 create_run（按 batch_size 切片），不是 manifest 总数——大于单次
        run 上限的清单正是分批投放的核心场景，创建/预检不再拒绝。批大小
        护栏（resolve_batch_size）才是该上限在 campaign 侧的判定面。"""
        monkeypatch.setattr(settings.executor_runtime.workflows, "max_items_per_run", 2)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-itemcap-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        items = [{"type": "material", "material_id": "mat-1"}] * 3
        row = campaign_service.create_campaign(workspace_id, "submit", items=items, batch_size=2)
        assert row["mode"] == "submit"
        assert row["status"] == "pending"
        assert row["batch_size"] == 2
        # preview 同口径：总数 3 直接如实计数，不再按清单级上限拒绝。
        preview = campaign_service.preview_campaign(
            workspace_id, "submit", items=items, batch_size=2
        )
        assert preview["total_items"] == 3
        assert preview["estimated_batches"] == 2

    def test_submit_batch_size_over_per_run_limit_still_rejected(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """上限的正确落点：submit 的 batch_size > max_items_per_run 仍拒绝
        （每批走 create_run 的 #358 上限——resolve_batch_size 的判定不变）。"""
        monkeypatch.setattr(settings.executor_runtime.workflows, "max_items_per_run", 2)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-itemcap-batch-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        items = [{"type": "material", "material_id": "mat-1"}] * 3
        with pytest.raises(InvalidOperationError, match="max_items_per_run"):
            campaign_service.create_campaign(workspace_id, "submit", items=items, batch_size=3)
        with pytest.raises(InvalidOperationError, match="max_items_per_run"):
            campaign_service.preview_campaign(workspace_id, "submit", items=items, batch_size=3)

    # ------------------------------------------------------------------
    # Round-3 P1: quota precheck ahead of the bucket PUT
    # ------------------------------------------------------------------

    def test_quota_refusal_leaves_no_bucket_object(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """三轮 P1：超 inline 阈值 + workspace 配额满——advisory 预检在 PUT
        之前拒绝，不再每次泄漏一个无引用的 manifest 对象（s3_jobs_gc 只扫
        jobs/ 与 jobs-staging/，不知道 campaign 引用，不会回收它）。"""
        config = settings.executor_runtime.campaigns
        monkeypatch.setattr(config, "manifest_inline_max_bytes", 64)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-quota-leak-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        ids = _seed_failed_jobs(job_db, workspace_id, 1)
        cap = config.max_active_per_workspace
        for _ in range(cap):
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
            )
        storage = FakeObjectStorage()
        campaign_service.object_storage = storage
        items = [
            {"type": "material", "material_id": "mat-1"},
            {"type": "material", "material_id": "mat-1"},
        ]
        with pytest.raises(ConflictError, match="active campaigns"):
            campaign_service.create_campaign(workspace_id, "submit", items=items)
        assert storage.puts == [], "over-quota create must not PUT a manifest object"
        assert storage.objects == {}

    def test_quota_precheck_only_applies_to_bucket_branch(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """预检只挡 bucket 分支：inline 清单在配额满时的错误仍由 guarded
        create 的 409 报出（不提前、不重复），且没有 PUT 发生。"""
        config = settings.executor_runtime.campaigns
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-quota-inline-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        ids = _seed_failed_jobs(job_db, workspace_id, 1)
        cap = config.max_active_per_workspace
        for _ in range(cap):
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
            )
        with pytest.raises(ConflictError, match="active campaigns"):
            campaign_service.create_campaign(
                workspace_id,
                "submit",
                items=[{"type": "material", "material_id": "mat-1"}],
            )

    # ------------------------------------------------------------------
    # Round-3 P2: GC-isolated manifest key prefix
    # ------------------------------------------------------------------

    def test_manifest_key_outside_job_gc_prefixes(self, job_db):
        """三轮 P2：manifest key 必须落在 s3_jobs_gc 的两个扫描前缀
        （jobs/ 与 jobs-staging/）之外——哪怕 workspace id 恰好撞名。GC 的
        引用集（job_artifacts ∪ materials）不含 campaign，撞名即误删。"""
        for workspace_id in ("jobs", "jobs-staging", "normal-ws"):
            key = campaign_manifest_key(workspace_id, "c-1")
            assert key.startswith(f"{CAMPAIGN_MANIFEST_KEY_PREFIX}/{workspace_id}/campaigns/")
            assert not key.startswith("jobs/") and not key.startswith("jobs-staging/")

    def test_manifest_key_prefix_pinned_in_storage_spec(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """落桶的 target_spec 真实携带新前缀（端到端钉住，不只是纯函数）。"""
        config = settings.executor_runtime.campaigns
        monkeypatch.setattr(config, "manifest_inline_max_bytes", 64)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-key-prefix-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        storage = FakeObjectStorage()
        campaign_service.object_storage = storage
        row = campaign_service.create_campaign(
            workspace_id,
            "submit",
            items=[{"type": "material", "material_id": "mat-1"}] * 2,
        )
        key = row["target_spec"]["manifest_storage_key"]
        assert key.startswith(f"{CAMPAIGN_MANIFEST_KEY_PREFIX}/{workspace_id}/")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class TestPreview:
    def test_rerun_preview_same_counts_as_batch_rerun_preview(self, campaign_service, job_db):
        """同函数即同数：campaign preview 与既有 preview 端点共享判定。"""
        from server.app.services.job_rerun.preview import batch_rerun_preview

        workspace_id = _seed_workspace_with_revision(job_db, "campaign-preview-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 7)
        direct = batch_rerun_preview(
            campaign_service.rerun_service,
            workspace_id,
            ids,
            "intake_knowledge_points",
        )
        via_campaign = campaign_service.preview_campaign(
            workspace_id, "rerun", job_ids=ids, node_key="intake_knowledge_points"
        )
        assert via_campaign["mode"] == "rerun"
        assert via_campaign["total_count"] == direct["total_count"] == 7
        assert via_campaign["eligible_count"] == direct["eligible_count"]

    def test_upgrade_preview_counts_stale_jobs(self, campaign_service, job_db):
        """审核 P2：upgrade preview 走升级资格判定——不带快照的 stale job
        全部 eligible（此前走 rerun preview 的 node_key 判定恒 0）。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-upgrade-preview-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 4)
        result = campaign_service.preview_campaign(workspace_id, "upgrade", job_ids=ids)
        assert result["mode"] == "upgrade"
        assert result["total_count"] == 4
        assert result["eligible_count"] == 4
        assert result["eligible_count"] > 0  # the P2 regression pin

    def test_upgrade_preview_current_jobs_ineligible(self, campaign_service, job_db):
        """与 upgrade 写路径同判定：revision pin + definition snapshot 都等于
        active revision 才算 current（只 pin 不算——stale snapshot 要 heal）。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-upgrade-current-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 3)
        active = job_db.get_active_workflow_revision(workspace_id, workspace_id)
        assert active is not None
        # current: pin AND snapshot both match.
        with job_db.connect() as conn:
            conn.execute(
                "update jobs set workflow_revision_id=%s, workflow_definition_snapshot_json=%s"
                " where id=%s",
                (str(active["id"]), str(active["definition_json"]), ids[0]),
            )
        # half-current: pin matches, snapshot stale — must stay eligible
        # (the write path re-pins to heal instead of skipping forever).
        with job_db.connect() as conn:
            conn.execute(
                "update jobs set workflow_revision_id=%s where id=%s",
                (str(active["id"]), ids[1]),
            )
        result = campaign_service.preview_campaign(workspace_id, "upgrade", job_ids=ids)
        assert result["total_count"] == 3
        assert result["eligible_count"] == 2

    def test_upgrade_preview_matches_upgrade_write_path(self, campaign_service, job_db):
        """preview 与真实路径共享判定：eligible 数与 JobWorkflowUpgradeService
        逐 job 实际尝试数一致（succeeded + failed；busy 是逐批次的 skip）。"""
        from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService

        workspace_id = _seed_workspace_with_revision(job_db, "campaign-upgrade-parity-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 5)
        preview = campaign_service.preview_campaign(workspace_id, "upgrade", job_ids=ids)
        upgrade_service = JobWorkflowUpgradeService(
            job_db, campaign_service.rerun_service.lease_repo
        )
        results = [upgrade_service.upgrade(workspace_id, job_id) for job_id in ids]
        attempted = [r for r in results if r["status"] in ("succeeded", "failed")]
        assert len(attempted) == preview["eligible_count"] == 5

    def test_upgrade_preview_no_active_revision(self, campaign_service, job_db):
        """无 active revision 的 workspace：upgrade 会失败每个 job，故 eligible 0。"""
        workspace = job_db.create_workspace(
            "campaign-no-revision-ws", default_workflow_key="campaign-no-revision-ws"
        )
        workspace_id = str(workspace["id"])
        _insert_job(job_db, workspace_id, "question", "Q-no-revision")
        result = campaign_service.preview_campaign(workspace_id, "upgrade", job_ids=["j-1"])
        assert result["mode"] == "upgrade"
        assert result["total_count"] == 1
        assert result["eligible_count"] == 0

    def test_rerun_preview_estimated_batches(self, campaign_service, job_db, settings):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-est-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 12)
        default_batch = settings.executor_runtime.campaigns.default_batch_size
        result = campaign_service.preview_campaign(
            workspace_id, "rerun", job_ids=ids, node_key="intake_knowledge_points"
        )
        assert result["estimated_batches"] == 1
        assert result["batch_size"] == default_batch
        small = campaign_service.preview_campaign(
            workspace_id,
            "rerun",
            job_ids=ids,
            node_key="intake_knowledge_points",
            batch_size=5,
        )
        assert small["estimated_batches"] == 3  # ceil(12/5)

    def test_submit_preview_dedup_probe(self, campaign_service, job_db):
        """submit preview：resolve + dedup 探测 → would_create / would_skip。

        入口契约收 material+ref（发布变体），ref item 才过 preview 的
        intake preflight（二轮 P1：与创建同判定）。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-subpreview-ws")
        _widen_start_item_types(job_db, workspace_id)
        _insert_material(job_db, workspace_id, "mat-1")
        _insert_connection(job_db, "cms-main")
        _insert_job(job_db, workspace_id, "material", "mat-1")
        result = campaign_service.preview_campaign(
            workspace_id,
            "submit",
            items=[
                {"type": "material", "material_id": "mat-1"},
                {"type": "ref", "connection_key": "cms-main", "external_id": "Q-9"},
            ],
        )
        assert result == {
            "mode": "submit",
            "total_items": 2,
            "would_create": 1,
            "would_skip": 1,
            "estimated_batches": 1,
            "batch_size": result["batch_size"],
        }

    def test_preview_writes_nothing(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-nowrite-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 3)
        campaign_service.preview_campaign(
            workspace_id, "rerun", job_ids=ids, node_key="intake_knowledge_points"
        )
        assert campaign_service.list_campaigns(workspace_id) == []

    def test_preview_rejects_unknown_mode(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-bogus-ws")
        with pytest.raises(InvalidOperationError, match="Unsupported campaign mode"):
            campaign_service.preview_campaign(workspace_id, "bogus", job_ids=["j"])

    def test_preview_rerun_batch_size_ceiling_shared_with_create(
        self, campaign_service, job_db, settings
    ):
        """审核二轮 P2：rerun/upgrade preview 复用创建的 batch_size 护栏——
        dry-run 不再确认一个随后无法创建的 batch_size。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-pv-ceiling-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 2)
        ceiling = settings.executor_runtime.campaigns.rerun_max_batch_size
        for mode in ("rerun", "upgrade"):
            with pytest.raises(InvalidOperationError, match="rerun_max_batch_size"):
                campaign_service.preview_campaign(
                    workspace_id,
                    mode,
                    job_ids=ids,
                    node_key="intake_knowledge_points" if mode == "rerun" else None,
                    batch_size=ceiling + 1,
                )

    def test_preview_submit_batch_size_ceiling_shared_with_create(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """审核二轮 P2：submit preview 的 batch_size 上限同创建
        （workflows.max_items_per_run）。"""
        monkeypatch.setattr(settings.executor_runtime.workflows, "max_items_per_run", 2)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-pv-subcap-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        with pytest.raises(InvalidOperationError, match="max_items_per_run"):
            campaign_service.preview_campaign(
                workspace_id,
                "submit",
                items=[{"type": "material", "material_id": "mat-1"}],
                batch_size=3,
            )

    def test_preview_batch_size_at_ceilings_not_overrejected(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """合法值不误拒：恰在上限的 batch_size，preview 与创建都放行。"""
        monkeypatch.setattr(settings.executor_runtime.workflows, "max_items_per_run", 2)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-pv-ok-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 2)
        _insert_material(job_db, workspace_id, "mat-1")
        ceiling = settings.executor_runtime.campaigns.rerun_max_batch_size
        rerun_preview = campaign_service.preview_campaign(
            workspace_id,
            "rerun",
            job_ids=ids,
            node_key="intake_knowledge_points",
            batch_size=ceiling,
        )
        assert rerun_preview["batch_size"] == ceiling
        submit_preview = campaign_service.preview_campaign(
            workspace_id,
            "submit",
            items=[{"type": "material", "material_id": "mat-1"}],
            batch_size=2,
        )
        assert submit_preview["batch_size"] == 2
        # 同参数创建同样成功（护栏口径一致的反向钉）。
        row = campaign_service.create_campaign(
            workspace_id,
            "rerun",
            job_ids=ids,
            node_key="intake_knowledge_points",
            batch_size=ceiling,
        )
        assert row["batch_size"] == ceiling

    # ------------------------------------------------------------------
    # Round-3 P2: preview shares the serialized-bytes ceiling with create
    # ------------------------------------------------------------------

    def test_submit_preview_over_serialized_bytes_rejected(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """三轮 P2：preview 与创建共用 serialize_manifest 后的字节上限——
        单 item 超大 params 的 payload，preview 报与创建相同的 413 形错误，
        不再 200 一个创建必拒的 campaign。"""
        config = settings.executor_runtime.campaigns
        monkeypatch.setattr(config, "manifest_max_bytes", 100)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-pv-bytes-ws")
        _widen_start_item_types(job_db, workspace_id)
        _insert_material(job_db, workspace_id, "mat-1")
        items = [{"type": "material", "material_id": "mat-1"}] * 3
        with pytest.raises(CampaignManifestTooLargeError):
            campaign_service.preview_campaign(workspace_id, "submit", items=items)
        # 创建路径同错误（两路径共享 check_manifest_bytes 的反向钉）。
        with pytest.raises(CampaignManifestTooLargeError):
            campaign_service.create_campaign(workspace_id, "submit", items=items)

    def test_submit_preview_within_bytes_not_overrejected(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """合法值不误拒：序列化在上限内的清单，preview 与创建都放行。"""
        config = settings.executor_runtime.campaigns
        monkeypatch.setattr(config, "manifest_max_bytes", 100)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-pv-bytes-ok-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        items = [{"type": "material", "material_id": "mat-1"}] * 2
        preview = campaign_service.preview_campaign(workspace_id, "submit", items=items)
        assert preview["total_items"] == 2
        assert (
            campaign_service.create_campaign(workspace_id, "submit", items=items)["mode"]
            == "submit"
        )
