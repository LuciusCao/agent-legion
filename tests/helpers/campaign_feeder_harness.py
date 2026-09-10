"""Campaign feeder 测试脚手架（#532 PR-B / PR #545 P1）。

test_campaign_feeder.py（行为）与 test_campaign_feeder_crash.py（PR #545
P1 崩溃窗口回归锁）共享的 seeding / 断言原语。测试模块不得互相 import
（tests/app/test_pytest_postgres_boundaries.py 的守卫），共享件落在这里。

同步驱动约定：两个文件都直接调 ``CampaignFeeder._tick()``（线程壳只是
wake/stop 管道），断言确定性、不与 2s 节奏竞速。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.run_service import RunService
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker.campaign_feeder import CampaignFeeder
from tests.helpers import publish_builtin_revision
from tests.postgres_support import TEST_DATABASE_URL

NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]


def make_job_db(tmp_path: Path) -> JobQueries:
    """conftest 层 job_db fixture 的等价物（helpers 不能声明 fixture）。"""
    return JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")


class RunningControl(WorkspaceWorkerControl):
    """测试替身：所有 workspace 一律「未暂停」。

    生产语义里 is_paused 是 fail-closed（未知 workspace 一律 paused，
    startup reset_all_to_paused 后由 API 逐个 resume），feeder 测试不测
    pause 门本身（那是 test_paused_workspace_suspends... 的职责，它显式
    传真的 control），默认替身让其余测试不必逐个 resume。
    """

    def is_paused(self, workspace_id: str) -> bool:  # noqa: ARG002
        return False


def make_feeder(
    job_db: JobQueries,
    settings: Any,
    *,
    control: WorkspaceWorkerControl | None = None,
) -> CampaignFeeder:
    leases = ExecutorLeaseRepository(job_db, data_dir=settings.data_dir)
    if control is None:
        # 默认未暂停（见 RunningControl）；显式 control 的测试自己管理。
        control = RunningControl()
    return CampaignFeeder(
        job_db,
        settings,
        rerun_service=JobRerunService(job_db, leases, settings),
        upgrade_service=JobWorkflowUpgradeService(job_db, leases),
        run_service=RunService(job_db, settings),
        workspace_worker_control=control,
    )


def workspace(job_db: JobQueries, name: str) -> str:
    """带内置 revision 的 workspace（definition 解析需要 active revision）。"""
    row = job_db.create_workspace(name, default_workflow_key="education_video_problems_generation")
    publish_builtin_revision(job_db, str(row["id"]))
    return str(row["id"])


def seed_failed_jobs(job_db: JobQueries, workspace_id: str, count: int, prefix: str) -> list[str]:
    """Failed jobs with a completed first node (rerunnable at that node)."""
    ids: list[str] = []
    for i in range(count):
        job = job_db.create_job(
            workflow_key="education_video_problems_generation",
            source_type="question",
            source_id=f"{prefix}{i}",
            run_id="",
            title=f"{prefix}{i}",
            node_keys=NODE_KEYS,
            workspace_id=workspace_id,
        )
        job_db.update_job_node(job["id"], NODE_KEYS[0], status="completed")
        job_db.update_job_status(job["id"], "failed", "boom")
        ids.append(str(job["id"]))
    return ids


def create_rerun_campaign(
    job_db: JobQueries,
    workspace_id: str,
    *,
    job_ids: list[str] | None = None,
    job_filter: dict[str, Any] | None = None,
    watermark: int = 1,
    batch_size: int = 5000,
) -> dict[str, Any]:
    target: dict[str, Any] = (
        {"filter": job_filter} if job_filter is not None else {"job_ids": sorted(job_ids)}
    )
    target["node_key"] = NODE_KEYS[0]
    return job_db.create_campaign(
        workspace_id,
        "rerun",
        target,
        watermark=watermark,
        batch_size=batch_size,
        progress=({"cursor": None, "processed": 0} if job_filter is not None else {"offset": 0}),
    )


def create_filter_campaign(
    job_db: JobQueries,
    workspace_id: str,
    *,
    batch_size: int,
) -> dict[str, Any]:
    """PR #545 P1 用的 status="failed" filter 形态，watermark=0 门全开。

    （level>=0 恒真会反向卡死，见 _feed_one 的 watermark>=1 前置——0 语
    义即禁用），崩溃批翻回 queued 的 job 不会像 watermark=1 那样把水位顶
    到线上、冻住 campaign 直到别的 job 排空。"""
    return job_db.create_campaign(
        workspace_id,
        "rerun",
        {"filter": {"status": "failed"}, "node_key": NODE_KEYS[0]},
        watermark=0,
        batch_size=batch_size,
        progress={"cursor": None, "processed": 0},
    )


def run_ticks(feeder: CampaignFeeder, rounds: int) -> None:
    """Drive the loop synchronously; clear per-tick pacing between rounds."""
    for _ in range(rounds):
        feeder._tick()
        feeder._next_feed_at.clear()  # skip the feed-interval pacing only


def queued_count(job_db: JobQueries, workspace_id: str) -> int:
    counts = job_db.count_jobs_by_status(workspace_id)
    return counts.get("pending", 0)
