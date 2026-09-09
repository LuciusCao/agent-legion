"""PR #545 P1 崩溃窗口回归锁：filter 批次先持久化「本批目标」再投递。

从 test_campaign_feeder.py 拆出（test 1000 行上限；脚手架在
tests/helpers/campaign_feeder_harness.py——测试模块不得互相 import）。

崩溃窗口：filter 的匹配字段被投递自身改写（rerun 的 status="failed"、
upgrade 的 status/workflow_version），进程在 ``_submit_batch`` 已提交而
``advance_campaign_progress`` 尚未落账时死亡——重启后 filter 重查选不出
已投递的 job（它们退出了匹配集），processed/jobs 计数永久漏记、甚至提前
判耗尽。修复：切片选取后先把批内 ids + next_cursor CAS 进
progress_json.pending_batch，投递完成、计数落账时摘除；重启时优先消化
它（重放被 rerun/upgrade 写路径守卫幂等吸收），计数在重放 pass 落账。

同步驱动约定与母文件一致：直接调 ``_tick()``，无 sleep 竞速。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from server.app.jobs import JobQueries
from server.app.workflow_worker.campaign_feeder import CampaignFeeder
from tests.helpers.campaign_feeder_harness import (
    NODE_KEYS,
    create_filter_campaign,
    make_feeder,
    queued_count,
    run_ticks,
    seed_failed_jobs,
    workspace,
)
from tests.postgres_support import TEST_DATABASE_URL

# The rerun smoke's job count. The design's 10^5 acceptance seed is a
# staging-scale calibration; at CI scale this file's whole postgres shard
# shares one worker and one database, so 600 failed jobs (2 batches of 300
# + verify batches) keeps the smoke meaningful (keyset slicing across
# multiple batches, restart mid-drain) at a cost the gate can bear.
_SMOKE_JOB_COUNT = 600
_SMOKE_BATCH_SIZE = 300


@pytest.fixture
def job_db(tmp_path: Path) -> JobQueries:
    return JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")


@pytest.fixture
def feeder(job_db, settings) -> CampaignFeeder:
    return make_feeder(job_db, settings)


def test_filter_crash_before_advance_resumes_without_loss_or_duplication(job_db, settings) -> None:
    """PR #545 P1 回归锁（rerun filter 形态）：批内 job 已提交（翻回
    queued、退出 status="failed" 匹配集）但 CAS 计数落账前崩溃——重启后
    修复前 filter 重查选不出这批 job（游标未推进也从这页起，但它们已不匹
    配），processed/jobs_succeeded 永久漏记、甚至提前判耗尽；修复后
    pending_batch 先于投递持久化，重启优先消化它：重放被写路径守卫幂等
    吸收（节点已 pending、job 已 queued，再次 mark 是同一状态），计数在
    重放 pass 落账——零漏记、零重复、零提前耗尽。"""
    ws = workspace(job_db, "feeder-p1-crash")
    seed_failed_jobs(job_db, ws, 6, "P1C")
    campaign = create_filter_campaign(job_db, ws, batch_size=3)
    campaign_id = campaign["id"]

    first = make_feeder(job_db, settings)
    # 模拟「提交完成、落账前」的进程死亡：只执行 _submit_batch（内部已
    # stage pending_batch 并投递），不走 _feed_one 的 advance。
    outcome = first._submit_batch(dict(campaign))
    assert outcome.succeeded == 3  # 本批已提交：3 个 job 翻回 queued
    row = job_db.get_campaign(campaign_id)
    assert row["batches_submitted"] == 0  # 落账没发生（崩溃）
    assert row["progress"]["cursor"] is None  # 游标未推进
    assert "pending_batch" in row["progress"]  # 本批目标已持久化
    assert len(row["progress"]["pending_batch"]["ids"]) == 3
    # 修复前这里是空：已投递的 job 退出匹配集，filter 查询选不出它们。
    assert queued_count(job_db, ws) == 3

    # 重启：全新 feeder（内存态全丢），从存储的 pending_batch 消化。
    second = make_feeder(job_db, settings)
    run_ticks(second, 5)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    # 零漏记：崩溃批的 3 个 + 后续 3 个全部计入，重放不再重复计。
    assert row["jobs_succeeded"] == 6
    assert row["jobs_skipped"] == 0
    assert row["jobs_failed"] == 0
    assert row["progress"]["processed"] == 6
    assert "pending_batch" not in row["progress"]  # 消化后摘除
    assert row["progress"]["cursor"] is None
    # 零重复：每个 job 恰好 queued 一次（重放的 mark 是幂等同状态写）。
    assert queued_count(job_db, ws) == 6


def test_filter_crash_before_advance_resume_endpoint_path(job_db, settings) -> None:
    """同一崩溃窗口走 pause/resume 竞态路径（advance CAS 失败而非进程死
    亡）：pending_batch 留在文档里，resume 回到 running 后 feeder 消化
    它——行为与崩溃重启一致，计数零漏（锁的是 _feed_one 的 advance 输给
    pause，批次与 pending_batch 都已落库）。"""
    ws = workspace(job_db, "feeder-p1-pause")
    seed_failed_jobs(job_db, ws, 2, "P1P")
    campaign = create_filter_campaign(job_db, ws, batch_size=2)
    campaign_id = campaign["id"]

    feeder = make_feeder(job_db, settings)
    original = feeder._submit_batch

    def _submit_then_pause(camp):
        outcome = original(camp)
        # 批已提交（含 pending_batch stage），advance 前被 pause 抢先。
        job_db.transition_campaign_status(str(camp["id"]), ("running",), "paused")
        return outcome

    feeder._submit_batch = _submit_then_pause
    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "paused"
    assert row["batches_submitted"] == 0
    assert "pending_batch" in row["progress"]
    assert queued_count(job_db, ws) == 2

    # resume → running：feeder 消化 pending_batch，计数落账后翻 completed。
    job_db.transition_campaign_status(campaign_id, ("paused",), "running")
    feeder._submit_batch = original
    run_ticks(feeder, 3)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 2
    assert row["progress"]["processed"] == 2
    assert "pending_batch" not in row["progress"]
    assert queued_count(job_db, ws) == 2


def test_filter_crash_before_advance_upgrade_mode(job_db, settings) -> None:
    """PR #545 P1 回归锁（upgrade filter 形态）：upgrade 的匹配字段同样
    被投递改写（status completed→queued、workflow_version 翻新）——同一
    stage/重放/落账窗口必须成立，3 个 stale job 计数零漏。"""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin import load_builtin_workflow

    ws = workspace(job_db, "feeder-p1-upgrade")
    definition = load_builtin_workflow("education_video_problems_generation")
    revisions = WorkflowRevisionService(job_db)
    original = revisions.publish_workspace_revision(ws, definition)
    current = revisions.publish_workspace_revision(ws, definition)
    for i in range(3):
        job = job_db.create_job(
            workflow_key="education_video_problems_generation",
            source_type="question",
            source_id=f"P1U{i}",
            run_id="",
            title=f"P1U{i}",
            node_keys=NODE_KEYS,
            workspace_id=ws,
            workflow_revision_id=original["id"],
            workflow_version=original["version"],
            workflow_definition_hash=original["definition_hash"],
            workflow_definition_snapshot_json=original["definition_json"],
        )
        job_db.update_job_status(job["id"], "completed")

    campaign = job_db.create_campaign(
        ws,
        "upgrade",
        {"filter": {"status": "completed"}},
        watermark=0,
        batch_size=10,
        progress={"cursor": None, "processed": 0},
    )
    campaign_id = campaign["id"]

    first = make_feeder(job_db, settings)
    calls: list[str] = []
    real_upgrade = first.upgrade_service.upgrade

    def _recording_upgrade(workspace_id, job_id):
        calls.append(job_id)
        return real_upgrade(workspace_id, job_id)

    first.upgrade_service.upgrade = _recording_upgrade
    outcome = first._submit_batch(dict(campaign))  # 提交后、落账前「崩溃」
    assert outcome.succeeded == 3
    assert len(calls) == 3  # 崩溃前每个 job 恰好 upgrade 一次
    assert job_db.get_campaign(campaign_id)["batches_submitted"] == 0

    second = make_feeder(job_db, settings)
    second.upgrade_service.upgrade = _recording_upgrade
    run_ticks(second, 3)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    # 计数语义与既有 explicit-ids rerun 崩溃先例一致（counters reflect
    # one pass）：重放 pass 观察到 already_current（upgrade 的幂等检查在
    # service 层，比对 revision id + 快照——崩溃 pass 已把它们翻新），落
    # 账 skipped=3 / succeeded=0。零漏不变量是 processed=3（三个 job 都
    # 被本 campaign 处理且只处理一批）+ 效果（全部 queued、pin 到新
    # revision）+ 零副作用重复（重放只把 pin/节点写成同样的新值，
    # len(calls)==2×3）。
    assert row["jobs_succeeded"] == 0
    assert row["jobs_skipped"] == 3
    assert row["progress"]["processed"] == 3
    assert "pending_batch" not in row["progress"]
    assert len(calls) == 6
    assert queued_count(job_db, ws) == 3
    for job_id in calls[:3]:
        upgraded = job_db.get_job(job_id)
        assert upgraded["status"] == "queued"
        assert upgraded["workflow_revision_id"] == current["id"]


def test_filter_batch_stage_lost_to_cancel_skips_feed(job_db, feeder) -> None:
    """stage 的 CAS 输给 cancel：批次不投（比旧的 pause 竞态语义更收紧
    ——旧窗口里批次已投出去才轮到 CAS），游标不动、无翻转、行终态。"""
    ws = workspace(job_db, "feeder-p1-cancel")
    seed_failed_jobs(job_db, ws, 2, "P1X")
    campaign = create_filter_campaign(job_db, ws, batch_size=2)
    campaign_id = campaign["id"]

    feeder_module = sys.modules[CampaignFeeder.__module__]
    real_batch_rerun = feeder_module.batch_rerun
    real_next_slice = feeder_module.next_slice  # from-import 绑定在 feeder 命名空间
    submitted: list[list[str]] = []

    def _spying_rerun(service, workspace_id, **kwargs):
        submitted.append(list(kwargs.get("job_ids") or []))
        return real_batch_rerun(service, workspace_id, **kwargs)

    def _slice_then_cancel(job_db_arg, camp):
        ids, next_cursor, exhausted = real_next_slice(job_db_arg, camp)
        # 切片已选出、stage 尚未执行：cancel 抢先落库（此时 pickup 已把
        # 行翻成 running，从 running 取消）。stage 的 CAS WHERE
        # (status in pending/running) 必输 → 批次被丢弃、不投递。
        job_db.transition_campaign_status(str(camp["id"]), ("running",), "cancelled")
        return ids, next_cursor, exhausted

    feeder_module.next_slice = _slice_then_cancel
    feeder_module.batch_rerun = _spying_rerun
    try:
        feeder._tick()
    finally:
        feeder_module.next_slice = real_next_slice
        feeder_module.batch_rerun = real_batch_rerun
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "cancelled"
    assert submitted == []  # 批次在 stage 失败后被丢弃，从未投递
    assert row["batches_submitted"] == 0
    assert queued_count(job_db, ws) == 0  # 无翻转
    # stage 的 CAS 落空没有留下半应用状态：pending_batch 不在文档里。
    assert "pending_batch" not in row["progress"]


def test_filter_empty_pending_batch_falls_through_to_fresh_query(job_db, feeder) -> None:
    """防御形状：progress 里残留空 ids 的 pending_batch（不应出现——
    stage 只在有 ids 时写入）时，恢复路径跳过它、退回正常 filter 查询，
    不炸、不卡死。"""
    ws = workspace(job_db, "feeder-p1-empty")
    seed_failed_jobs(job_db, ws, 2, "P1E")
    campaign = create_filter_campaign(job_db, ws, batch_size=5)
    campaign_id = campaign["id"]
    feeder.job_db.advance_campaign_progress(
        campaign_id,
        expected_progress={"cursor": None, "processed": 0},
        progress={"cursor": None, "processed": 0, "pending_batch": {"ids": []}},
        batches_submitted=0,
        jobs_succeeded=0,
        jobs_skipped=0,
        jobs_failed=0,
    )

    run_ticks(feeder, 2)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 2
    assert "pending_batch" not in row["progress"]


def test_large_rerun_campaign_smoke(job_db, settings) -> None:
    """The #532 acceptance smoke, CI-scaled: 600 failed jobs (2 batches of
    300), kill the feeder midway, restart, drain — every job flipped exactly
    once, counters consistent, completed. (10^5 is the staging calibration;
    the pass-time invariant it protects — no slow-pass — is owned by the
    scheduler's own tests; here we pin the drain correctness the feeder
    adds.)"""
    ws = workspace(job_db, "feeder-smoke")
    seed_failed_jobs(job_db, ws, _SMOKE_JOB_COUNT, "SMK")
    campaign = job_db.create_campaign(
        ws,
        "rerun",
        {"filter": {"status": "failed"}, "node_key": NODE_KEYS[0]},
        watermark=_SMOKE_JOB_COUNT + 10,  # gate wide open
        batch_size=_SMOKE_BATCH_SIZE,
        progress={"cursor": None, "processed": 0},
    )
    campaign_id = campaign["id"]

    first = make_feeder(job_db, settings)
    run_ticks(first, 1)
    row = job_db.get_campaign(campaign_id)
    assert row["batches_submitted"] == 1
    assert row["jobs_succeeded"] == _SMOKE_BATCH_SIZE

    second = make_feeder(job_db, settings)  # the "restart"
    run_ticks(second, 3)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == _SMOKE_JOB_COUNT
    assert row["jobs_skipped"] == 0
    assert row["progress"]["processed"] == _SMOKE_JOB_COUNT
    assert queued_count(job_db, ws) == _SMOKE_JOB_COUNT
