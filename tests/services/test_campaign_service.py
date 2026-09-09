"""CampaignService: create / preview / state transitions at the service layer.

Pins the fail-fast creation contract (knob guards, active-campaign cap,
target resolution before the row exists, inline-vs-bucket storage decision),
the preview judgements (rerun preview delegates to batch_rerun_preview — the
same function the existing preview endpoint runs; submit preview resolves
items + dedup probe), and the pause/resume/cancel CAS semantics. API-level
auth matrix lives in tests/routes/test_campaigns_api.py.
"""

from __future__ import annotations

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.campaign_manifest import ManifestError
from server.app.services.campaign_service import (
    CampaignManifestTooLargeError,
    CampaignService,
    CampaignStorageUnavailableError,
    campaign_manifest_key,
)
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.job_rerun import JobRerunService
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

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
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
    rerun = JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
    )
    return CampaignService(job_db, settings, rerun_service=rerun)


# ---------------------------------------------------------------------------
# Create: knob guards
# ---------------------------------------------------------------------------


class TestCreateGuards:
    def test_unknown_mode_rejected(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-guards-ws")
        with pytest.raises(InvalidOperationError, match="Unsupported campaign mode"):
            campaign_service.create_campaign(workspace_id, "bogus", job_ids=["j-1"])

    def test_non_positive_watermark_rejected(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-guards-ws")
        with pytest.raises(InvalidOperationError, match="watermark"):
            campaign_service.create_campaign(workspace_id, "rerun", job_ids=["j-1"], watermark=0)

    def test_rerun_batch_size_ceiling(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-guards-ws")
        with pytest.raises(InvalidOperationError, match="rerun_max_batch_size"):
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=["j-1"], batch_size=5_001
            )

    def test_low_watermark_with_large_batch_allowed(self, campaign_service, job_db):
        """水位线是补货触发阈值而非容量承诺：低水位线+大批次合法。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-burst-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 2)
        row = campaign_service.create_campaign(
            workspace_id,
            "rerun",
            job_ids=ids,
            node_key="intake_knowledge_points",
            watermark=1,
            batch_size=5_000,
        )
        assert row["watermark"] == 1
        assert row["batch_size"] == 5_000

    def test_active_cap_enforced(self, campaign_service, job_db, settings):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-cap-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 4)
        cap = settings.executor_runtime.campaigns.max_active_per_workspace
        for _ in range(cap):
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
            )
        with pytest.raises(ConflictError, match="active campaigns"):
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
            )
        # Terminal campaigns free the slot.
        listed = campaign_service.list_campaigns(workspace_id)
        campaign_service.cancel_campaign(workspace_id, listed[0]["id"])
        campaign_service.create_campaign(
            workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
        )

    def test_concurrent_create_cannot_exceed_cap(self, campaign_service, job_db, settings):
        """审核 P2：并发创建竞态。两个 create 同时发起（最后一席之争）：
        guarded 事务在 workspace advisory lock 上串行——后进者在 count 处
        重新数到先进者已提交的行并拒绝。旧实现（事务外 count → 各自 INSERT）
        两个都读到 cap-1，双双落行击穿上限。"""
        import threading

        workspace_id = _seed_workspace_with_revision(job_db, "campaign-race-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 1)
        cap = settings.executor_runtime.campaigns.max_active_per_workspace
        # cap-1 个先行占位，让并发窗口决定最后一个名额。
        for _ in range(cap - 1):
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
            )
        assert job_db.count_active_campaigns(workspace_id) == cap - 1

        start = threading.Barrier(2, timeout=10)
        results: list = []
        errors: list = []

        def _run() -> None:
            try:
                start.wait(timeout=10)
                results.append(
                    campaign_service.create_campaign(
                        workspace_id,
                        "rerun",
                        job_ids=ids[:1],
                        node_key="intake_knowledge_points",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - collected for the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert not any(thread.is_alive() for thread in threads), "concurrent creates hung"
        # 精确上限：两个并发 create 只落一个行，另一个拿到 409 形 ConflictError。
        assert job_db.count_active_campaigns(workspace_id) == cap, (
            "concurrent creates broke the cap:"
            f" active={job_db.count_active_campaigns(workspace_id)}"
            f" results={len(results)} errors={[str(e) for e in errors]}"
        )
        assert len(results) == 1
        assert len(errors) == 1 and isinstance(errors[0], ConflictError)

    def test_pause_refill_resume_blocked_at_cap(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """审核 P2：pause 后名额被新 create 补满，resume 必须被拒——
        paused 行不在 active 集里，resume 的 count 必须数到补满后的集合。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-resume-cap-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 2)
        cap = settings.executor_runtime.campaigns.max_active_per_workspace
        # 占满上限后暂停一个。
        created = [
            campaign_service.create_campaign(
                workspace_id, "rerun", job_ids=ids[:1], node_key="intake_knowledge_points"
            )
            for _ in range(cap)
        ]
        paused = created[0]
        assert campaign_service.pause_campaign(workspace_id, paused["id"])["status"] == "paused"
        # 暂停腾出的名额被补满（创建恢复原上限数的 active 行）。
        campaign_service.create_campaign(
            workspace_id, "rerun", job_ids=ids[1:], node_key="intake_knowledge_points"
        )
        assert job_db.count_active_campaigns(workspace_id) == cap
        # 补满后 resume：被拒（409 形 ConflictError）。
        with pytest.raises(ConflictError, match="active campaigns"):
            campaign_service.resume_campaign(workspace_id, paused["id"])
        # 腾出名额后 resume 成功。
        listed = campaign_service.list_campaigns(workspace_id)
        campaign_service.cancel_campaign(workspace_id, listed[0]["id"])
        assert campaign_service.resume_campaign(workspace_id, paused["id"])["status"] == "running"


# ---------------------------------------------------------------------------
# Create: rerun target
# ---------------------------------------------------------------------------


class TestCreateRerunTarget:
    def test_explicit_ids_snapshotted(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-rerun-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 3)
        row = campaign_service.create_campaign(
            workspace_id, "rerun", job_ids=ids, node_key="intake_knowledge_points"
        )
        assert row["mode"] == "rerun"
        assert row["status"] == "pending"
        assert row["target_spec"]["job_ids"] == sorted(ids)
        assert row["target_spec"]["node_key"] == "intake_knowledge_points"

    def test_filter_form_preserved(self, campaign_service, job_db):
        """filter 形态只存 filter 本身（设计 §1.4：10^5 ids 不物化进行宽）。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-filter-ws")
        _seed_failed_jobs(job_db, workspace_id, 2)
        row = campaign_service.create_campaign(
            workspace_id,
            "rerun",
            job_filter=JobListFilter(status="failed"),
            node_key="intake_knowledge_points",
        )
        assert row["target_spec"]["filter"]["status"] == "failed"
        assert "job_ids" not in row["target_spec"]
        assert row["target_spec"]["node_key"] == "intake_knowledge_points"

    def test_both_ids_and_filter_rejected(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-either-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 1)
        with pytest.raises(InvalidOperationError, match="exactly one"):
            campaign_service.create_campaign(
                workspace_id,
                "rerun",
                job_ids=ids,
                job_filter=JobListFilter(status="failed"),
                node_key="intake_knowledge_points",
            )

    def test_node_key_xor_from_failed_node(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-xor-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 1)
        with pytest.raises(InvalidOperationError, match="node_key is required"):
            campaign_service.create_campaign(workspace_id, "rerun", job_ids=ids)
        with pytest.raises(InvalidOperationError, match="must be None"):
            campaign_service.create_campaign(
                workspace_id,
                "rerun",
                job_ids=ids,
                node_key="intake_knowledge_points",
                from_failed_node=True,
            )

    def test_empty_selection_rejected(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-empty-ws")
        with pytest.raises(InvalidOperationError, match="zero jobs"):
            campaign_service.create_campaign(
                workspace_id,
                "rerun",
                job_filter=JobListFilter(status="failed"),
                node_key="intake_knowledge_points",
            )

    def test_upgrade_mode_omits_rerun_knobs(self, campaign_service, job_db):
        """upgrade 与 rerun 同族取片，但不带 node_key/from_failed_node 语义。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-upgrade-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 2)
        row = campaign_service.create_campaign(workspace_id, "upgrade", job_ids=ids)
        assert row["mode"] == "upgrade"
        assert "node_key" not in row["target_spec"]
        assert "from_failed_node" not in row["target_spec"]


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

    def test_submit_manifest_over_per_run_limit_rejected(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """审核二轮 P1：manifest 数量超 workflows.max_items_per_run 时创建即拒
        （feeder 每批走 create_run 的 #358 上限——预检同判定）。"""
        monkeypatch.setattr(settings.executor_runtime.workflows, "max_items_per_run", 2)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-itemcap-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        items = [{"type": "material", "material_id": "mat-1"}] * 3
        with pytest.raises(InvalidOperationError, match="max_items_per_run"):
            campaign_service.create_campaign(workspace_id, "submit", items=items)
        assert campaign_service.list_campaigns(workspace_id) == []

    def test_submit_within_per_run_limit_not_overrejected(
        self, campaign_service, job_db, settings, monkeypatch
    ):
        """合法值不误拒：恰好等于 max_items_per_run 的 manifest 通过预检
        （batch_size 同步给到上限内——submit 的默认批大小此时也会被上限
        拦下，属创建护栏的正确口径）。"""
        monkeypatch.setattr(settings.executor_runtime.workflows, "max_items_per_run", 2)
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-itemcap-ok-ws")
        _insert_material(job_db, workspace_id, "mat-1")
        row = campaign_service.create_campaign(
            workspace_id,
            "submit",
            items=[{"type": "material", "material_id": "mat-1"}] * 2,
            batch_size=2,
        )
        assert row["mode"] == "submit"
        assert row["status"] == "pending"


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


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    def _make(self, campaign_service, job_db, workspace_id: str) -> str:
        ids = _seed_failed_jobs(job_db, workspace_id, 1)
        return campaign_service.create_campaign(
            workspace_id, "rerun", job_ids=ids, node_key="intake_knowledge_points"
        )["id"]

    def test_get_unknown_campaign_404(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-404-ws")
        with pytest.raises(NotFoundError):
            campaign_service.get_campaign(workspace_id, "nope")

    def test_pause_resume_cycle(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-cycle-ws")
        campaign_id = self._make(campaign_service, job_db, workspace_id)
        paused = campaign_service.pause_campaign(workspace_id, campaign_id)
        assert paused["status"] == "paused"
        resumed = campaign_service.resume_campaign(workspace_id, campaign_id)
        assert resumed["status"] == "running"
        # A running campaign cannot be resumed again.
        with pytest.raises(ConflictError, match="paused"):
            campaign_service.resume_campaign(workspace_id, campaign_id)

    def test_cancel_from_running_and_terminal_refusal(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-cancel-ws")
        campaign_id = self._make(campaign_service, job_db, workspace_id)
        cancelled = campaign_service.cancel_campaign(workspace_id, campaign_id)
        assert cancelled["status"] == "cancelled"
        assert cancelled["finished_at"] is not None
        with pytest.raises(ConflictError, match="already cancelled"):
            campaign_service.cancel_campaign(workspace_id, campaign_id)
        with pytest.raises(ConflictError, match="terminal"):
            campaign_service.pause_campaign(workspace_id, campaign_id)
        with pytest.raises(ConflictError, match="terminal"):
            campaign_service.resume_campaign(workspace_id, campaign_id)

    def test_cancel_from_paused(self, campaign_service, job_db):
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-pcancel-ws")
        campaign_id = self._make(campaign_service, job_db, workspace_id)
        campaign_service.pause_campaign(workspace_id, campaign_id)
        assert campaign_service.cancel_campaign(workspace_id, campaign_id)["status"] == "cancelled"

    def test_workspace_isolation(self, campaign_service, job_db):
        """跨 workspace 的 campaign id 不可见（404 而非 403——防枚举）。"""
        ws_a = _seed_workspace_with_revision(job_db, "campaign-iso-a")
        ws_b = _seed_workspace_with_revision(job_db, "campaign-iso-b")
        campaign_id = self._make(campaign_service, job_db, ws_a)
        with pytest.raises(NotFoundError):
            campaign_service.get_campaign(ws_b, campaign_id)
        with pytest.raises(NotFoundError):
            campaign_service.pause_campaign(ws_b, campaign_id)
