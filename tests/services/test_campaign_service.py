"""CampaignService: create guards / rerun target / state transitions.

Pins the fail-fast creation contract (knob guards, active-campaign cap,
rerun target resolution before the row exists), and the pause/resume/cancel
CAS semantics. The submit target lifecycle, the preview judgements, and
their review pins live in the sibling file
tests/services/test_campaign_service_submit.py (PR #541 round-3 P1 split,
zero-churn migration). API-level auth matrix lives in
tests/routes/test_campaigns_api.py.
"""

from __future__ import annotations

import pytest

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.campaign_service import CampaignService
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from tests.helpers import publish_builtin_revision

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]


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


class _SpyReadConnection:
    """记录 execute (sql, params) 的只读连接代理（F1 探测断言：filter 创建
    对 jobs 表只允许一次有界存在性探测，不得出现 1001/页的全量物化取片）。"""

    def __init__(self, conn, statements: list[tuple[str, tuple]]) -> None:
        self._conn = conn
        self._statements = statements

    def execute(self, sql, params=()):  # noqa: ANN001 - psycopg 连接鸭子类型
        self._statements.append((str(sql), tuple(params or ())))
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


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

    def test_filter_create_probes_existence_without_materializing_ids(
        self, campaign_service, job_db, monkeypatch
    ):
        """四轮 P2（F1）：filter 形态创建只做有界存在性探测——
        resolve_job_ids 的 O(N) keyset 全扫（10^5+ 选集的同步扫描与内存
        物化）不得进入该路径；探测复用取片查询（同一 filter 谓词），
        单页 limit=1（参数 2），而非 1001/页的物化取片。"""
        import contextlib

        from server.app.services import job_selection_resolver

        workspace_id = _seed_workspace_with_revision(job_db, "campaign-probe-ws")
        _seed_failed_jobs(job_db, workspace_id, 3)

        # 全量物化器被调用即失败：创建只落 filter，不需要 id 列表。
        def _no_materialize(*args, **kwargs):  # pragma: no cover - 断言绊线
            raise AssertionError("filter create must not materialize the full id list")

        monkeypatch.setattr(job_selection_resolver, "resolve_job_ids", _no_materialize)

        statements: list[tuple[str, tuple]] = []
        original_connect_read = job_db._connect_read

        @contextlib.contextmanager
        def _spying_connect_read():
            with original_connect_read() as conn:
                yield _SpyReadConnection(conn, statements)

        monkeypatch.setattr(job_db, "_connect_read", _spying_connect_read)

        row = campaign_service.create_campaign(
            workspace_id,
            "rerun",
            job_filter=JobListFilter(status="failed"),
            node_key="intake_knowledge_points",
        )
        assert row["target_spec"]["filter"]["status"] == "failed"
        # filter 创建全程只有这一次 jobs 读——探测本身。
        jobs_reads = [s for s in statements if " from jobs" in s[0]]
        assert len(jobs_reads) == 1, statements
        sql, params = jobs_reads[0]
        # 与 feeder 取片同一查询形态、同一 filter 谓词（status = %s）。
        assert "order by created_at desc, id desc" in sql
        assert "status = %s" in sql
        # 有界探测：limit 参数是 2（limit=1 的取片页 +1），不是物化页的
        # _PAGE_SIZE+1=1001——一次读最多 2 行，不随选集大小增长。
        assert params[-1] == 2, (sql, params)

    def test_explicit_ids_normalized_before_persist(self, campaign_service, job_db):
        """四轮 P2（F2）：显式 id 持久化前规范化（strip → 去空 → 去重，
        与 batch rerun/upgrade 写路径同处理）——" id" 与 "id" 不再落成
        两条快照（跨批次对同一 job 重复操作），空串不进 target_spec。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-normalize-ws")
        first, second = _seed_failed_jobs(job_db, workspace_id, 2)
        row = campaign_service.create_campaign(
            workspace_id,
            "rerun",
            job_ids=[f"  {first}  ", first, f" {second}", "", "   "],
            node_key="intake_knowledge_points",
        )
        assert row["target_spec"]["job_ids"] == sorted({first, second})

    def test_whitespace_only_ids_rejected_as_empty_selection(self, campaign_service, job_db):
        """全空白 id 列表按空选集拒绝（与空 filter 同一错误语义），不再
        建成 preview total_count=0 的无效 campaign。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-blank-ids-ws")
        with pytest.raises(InvalidOperationError, match="zero jobs"):
            campaign_service.create_campaign(
                workspace_id,
                "rerun",
                job_ids=["  ", ""],
                node_key="intake_knowledge_points",
            )

    def test_preview_counts_follow_normalized_ids(self, campaign_service, job_db):
        """preview 与创建共用规范化后的选集：脏 id（空白/重复）的 dry-run
        total_count 等于去重后的集合——空格差异不会把同一 job 数两次。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-preview-norm-ws")
        first, second = _seed_failed_jobs(job_db, workspace_id, 2)
        result = campaign_service.preview_campaign(
            workspace_id,
            "rerun",
            job_ids=[f" {first}", first, second],
            node_key="intake_knowledge_points",
        )
        assert result["total_count"] == 2

    def test_upgrade_mode_omits_rerun_knobs(self, campaign_service, job_db):
        """upgrade 与 rerun 同族取片，但不带 node_key/from_failed_node 语义。"""
        workspace_id = _seed_workspace_with_revision(job_db, "campaign-upgrade-ws")
        ids = _seed_failed_jobs(job_db, workspace_id, 2)
        row = campaign_service.create_campaign(workspace_id, "upgrade", job_ids=ids)
        assert row["mode"] == "upgrade"
        assert "node_key" not in row["target_spec"]
        assert "from_failed_node" not in row["target_spec"]


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
