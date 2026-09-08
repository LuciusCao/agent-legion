"""Campaign feeder behavior tests (#532 PR-B, design §2).

Covers the feeder loop's gates and drains against the real database: the
wide-set watermark gate (below the line feeds, at/above it holds), the
per-workspace fairness rule (one campaign, one batch per round, round-robin
across rounds), the pending→running CAS pickup race (two feeder instances,
one winner), crash-resume zero-double-flip (kill mid-campaign → restart →
the rerun counter equals the slice sum exactly), the paused-workspace
suspension (campaign stalls but never flips to paused itself), transient
vs deterministic failure routing, and the large-campaign rerun smoke.

The feeder's tick loop is driven synchronously: tests construct the
CampaignFeeder and call the private ``_tick()`` (the thread wrapper is
trivial wake/stop plumbing) so every assertion is deterministic — no sleeps
racing the 2s cadence. Tick-level unit behavior (backoff math, wake) is
pinned directly where needed.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.run_service import RunService
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflow_worker.campaign_feeder import CampaignFeeder
from tests.helpers import publish_builtin_revision
from tests.postgres_support import TEST_DATABASE_URL

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]

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
    return _make_feeder(job_db, settings)


def _make_feeder(
    job_db: JobQueries,
    settings: Any,
    *,
    control: WorkspaceWorkerControl | None = None,
) -> CampaignFeeder:
    leases = ExecutorLeaseRepository(job_db, data_dir=settings.data_dir)
    return CampaignFeeder(
        job_db,
        settings,
        rerun_service=JobRerunService(job_db, leases, settings),
        upgrade_service=JobWorkflowUpgradeService(job_db, leases),
        run_service=RunService(job_db, settings),
        workspace_worker_control=control,
    )


def _workspace(job_db: JobQueries, name: str) -> str:
    workspace = job_db.create_workspace(
        name, default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, str(workspace["id"]))
    return str(workspace["id"])


def _seed_failed_jobs(job_db: JobQueries, workspace_id: str, count: int, prefix: str) -> list[str]:
    """Failed jobs with a completed first node (rerunnable at that node)."""
    ids: list[str] = []
    for i in range(count):
        job = job_db.create_job(
            workflow_key="education_video_problems_generation",
            source_type="question",
            source_id=f"{prefix}{i}",
            run_id="",
            title=f"{prefix}{i}",
            node_keys=_NODE_KEYS,
            workspace_id=workspace_id,
        )
        job_db.update_job_node(job["id"], _NODE_KEYS[0], status="completed")
        job_db.update_job_status(job["id"], "failed", "boom")
        ids.append(str(job["id"]))
    return ids


def _create_rerun_campaign(
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
    target["node_key"] = _NODE_KEYS[0]
    return job_db.create_campaign(
        workspace_id,
        "rerun",
        target,
        watermark=watermark,
        batch_size=batch_size,
        progress=({"cursor": None, "processed": 0} if job_filter is not None else {"offset": 0}),
    )


def _run_ticks(feeder: CampaignFeeder, rounds: int) -> None:
    """Drive the loop synchronously; clear per-tick pacing between rounds."""
    for _ in range(rounds):
        feeder._tick()
        feeder._next_feed_at.clear()  # skip the feed-interval pacing only


def _queued_count(job_db: JobQueries, workspace_id: str) -> int:
    counts = job_db.count_jobs_by_status(workspace_id)
    return counts.get("pending", 0)


# ---------------------------------------------------------------------------
# Watermark gate
# ---------------------------------------------------------------------------


def test_watermark_below_line_feeds(job_db, feeder) -> None:
    """The watermark is a replenishment trigger: below it the batch goes out
    (with the level sampled into the trail); the low-watermark-plus-large-
    batch burst configuration is legal and never refused."""
    ws = _workspace(job_db, "feeder-wm")
    ids = _seed_failed_jobs(job_db, ws, 3, "WM")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=3)
    campaign_id = campaign["id"]

    feeder._tick()  # level 0 < 100: the whole target in one batch
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"  # exhausted in the same feed
    assert row["batches_submitted"] == 1
    assert row["jobs_succeeded"] == 3
    assert len(row["progress"]["watermark_samples"]) == 1
    assert row["progress"]["watermark_samples"][0]["level"] == 0
    # The flipped jobs are now non-terminal: the wide-set level counts them.
    assert _non_terminal_level(job_db, ws) == 3


def _non_terminal_level(job_db: JobQueries, workspace_id: str) -> int:
    return sum(
        n
        for status, n in job_db.count_jobs_by_status(workspace_id).items()
        if status not in ("completed", "failed")
    )


def test_watermark_gate_holds_batch_at_line(job_db, feeder) -> None:
    """Level == watermark holds: the campaign stays running with its cursor
    frozen until the level drains below the line."""
    ws = _workspace(job_db, "feeder-hold")
    ids = _seed_failed_jobs(job_db, ws, 4, "HOLD")
    # Seed 3 unrelated non-terminal jobs: level 3 == watermark 3.
    for i in range(3):
        job = job_db.create_job(
            workflow_key="education_video_problems_generation",
            source_type="question",
            source_id=f"HOLDX{i}",
            run_id="",
            title=f"HOLDX{i}",
            node_keys=_NODE_KEYS,
            workspace_id=ws,
        )
        job_db.update_job_status(job["id"], "running")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=3, batch_size=10)
    campaign_id = campaign["id"]

    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "running"  # picked up, but not fed
    assert row["batches_submitted"] == 0
    assert _queued_count(job_db, ws) == 0  # nothing flipped

    # The level drains (one running job completes): the gate opens.
    seeded = job_db.list_jobs(workspace_id=ws, limit=100)
    for job in seeded:
        if job["status"] == "running":
            job_db.update_job_status(job["id"], "completed")
            break
    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["batches_submitted"] == 1
    assert row["jobs_succeeded"] == 4
    assert row["status"] == "completed"  # one batch covered all four


# ---------------------------------------------------------------------------
# Fairness: one batch per workspace per round, round-robin across rounds
# ---------------------------------------------------------------------------


def test_one_batch_per_workspace_per_round(job_db, feeder) -> None:
    """Two campaigns in one workspace: each tick feeds at most one of them a
    single batch, and the round-robin pointer alternates across ticks."""
    ws = _workspace(job_db, "feeder-fair")
    ids_a = _seed_failed_jobs(job_db, ws, 4, "FA")
    ids_b = _seed_failed_jobs(job_db, ws, 4, "FB")
    # ids sorted: campaign A drains a-then-b batches; ids interleave by
    # sort order of the job ids, so assert on the SUM per round, not which.
    a = _create_rerun_campaign(job_db, ws, job_ids=ids_a, watermark=100, batch_size=1)
    b = _create_rerun_campaign(job_db, ws, job_ids=ids_b, watermark=100, batch_size=1)

    feeder._tick()
    rows = {c["id"]: job_db.get_campaign(c["id"]) for c in (a, b)}
    fed = [row for row in rows.values() if row["batches_submitted"] == 1]
    assert len(fed) == 1  # exactly one campaign got one batch
    assert sum(row["batches_submitted"] for row in rows.values()) == 1
    assert _queued_count(job_db, ws) == 1  # one flip, one batch

    feeder._next_feed_at.clear()
    feeder._tick()  # next round: the OTHER campaign feeds
    rows = {c["id"]: job_db.get_campaign(c["id"]) for c in (a, b)}
    assert all(row["batches_submitted"] == 1 for row in rows.values())
    assert _queued_count(job_db, ws) == 2


def test_feed_interval_paces_one_campaign(job_db, feeder) -> None:
    """A fed campaign waits out feed_interval_seconds before its next batch —
    without clearing the pacing, ticks hold; the wake call does not bypass
    the pacing (it only shortens the tick sleep)."""
    ws = _workspace(job_db, "feeder-pace")
    ids = _seed_failed_jobs(job_db, ws, 3, "PACE")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=1)
    campaign_id = campaign["id"]

    feeder._tick()
    assert job_db.get_campaign(campaign_id)["batches_submitted"] == 1
    feeder.wake()
    feeder._tick()  # still inside the feed-interval window
    assert job_db.get_campaign(campaign_id)["batches_submitted"] == 1
    feeder._next_feed_at.clear()
    feeder._tick()
    assert job_db.get_campaign(campaign_id)["batches_submitted"] == 2


def test_two_workspaces_feed_independently(job_db, feeder) -> None:
    """Workspace-scoped fairness: two workspaces each feed one batch in the
    same tick — neither waits for the other's campaign."""
    ws_a = _workspace(job_db, "feeder-ws-a")
    ws_b = _workspace(job_db, "feeder-ws-b")
    ids_a = _seed_failed_jobs(job_db, ws_a, 2, "WA")
    ids_b = _seed_failed_jobs(job_db, ws_b, 2, "WB")
    a = _create_rerun_campaign(job_db, ws_a, job_ids=ids_a, watermark=100, batch_size=1)
    b = _create_rerun_campaign(job_db, ws_b, job_ids=ids_b, watermark=100, batch_size=1)

    feeder._tick()
    assert job_db.get_campaign(a["id"])["batches_submitted"] == 1
    assert job_db.get_campaign(b["id"])["batches_submitted"] == 1


# ---------------------------------------------------------------------------
# CAS pickup race
# ---------------------------------------------------------------------------


def test_pending_to_running_pickup_race_one_winner(job_db, settings) -> None:
    """Two feeder instances against one pending campaign: the CAS pickup
    leaves exactly one winner — the loser's transition returns None and it
    feeds nothing (no double batch, no exception)."""
    ws = _workspace(job_db, "feeder-race")
    ids = _seed_failed_jobs(job_db, ws, 2, "RACE")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
    campaign_id = campaign["id"]

    feeder_one = _make_feeder(job_db, settings)
    feeder_two = _make_feeder(job_db, settings)
    feeder_one._tick()
    feeder_two._tick()

    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"  # drained by exactly one winner
    assert row["batches_submitted"] == 1  # exactly one batch total
    assert row["jobs_succeeded"] == 2
    assert _queued_count(job_db, ws) == 2  # each job flipped exactly once


def test_pickup_cas_loses_to_cancel(job_db, feeder) -> None:
    """A cancel landing between the scan and the pickup wins: the CAS flip
    misses, the feeder feeds nothing, and the row stays cancelled."""
    ws = _workspace(job_db, "feeder-cancel-race")
    ids = _seed_failed_jobs(job_db, ws, 2, "CR")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
    campaign_id = campaign["id"]
    # Simulate the interleaving: the scan's snapshot exists (the campaign
    # dict below), but the row is already cancelled by the time the pickup
    # runs.
    snapshot = dict(campaign)
    job_db.transition_campaign_status(campaign_id, ("pending",), "cancelled")

    feeder._feed_one(snapshot, 0)

    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "cancelled"
    assert row["batches_submitted"] == 0
    assert _queued_count(job_db, ws) == 0


def test_advance_cas_loses_to_pause(job_db, feeder) -> None:
    """A pause landing between the feed and the cursor advance wins: the
    batch's flips stay (idempotent), the cursor freezes, and the campaign
    shows paused with un-advanced counters."""
    ws = _workspace(job_db, "feeder-pause-race")
    ids = _seed_failed_jobs(job_db, ws, 3, "PR")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
    campaign_id = campaign["id"]

    original = feeder._submit_batch
    paused = {}

    def _submit_then_pause(camp):
        outcome = original(camp)
        # The pause lands after the batch's writes but before the CAS
        # advance — the advance must then miss (expected_progress no longer
        # matches: status left the active set first, so the WHERE fails).
        paused["row"] = job_db.transition_campaign_status(str(camp["id"]), ("running",), "paused")
        return outcome

    feeder._submit_batch = _submit_then_pause
    feeder._feed_one(dict(campaign), 0)

    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "paused"
    assert paused["row"] is not None
    # The batch's flips stay; the counters/cursor do not advance.
    assert row["batches_submitted"] == 0
    assert row["jobs_succeeded"] == 0
    assert _queued_count(job_db, ws) == 3


# ---------------------------------------------------------------------------
# Kill mid-campaign → restart → zero double flips
# ---------------------------------------------------------------------------


def test_kill_midway_restart_resumes_without_double_flips(job_db, settings) -> None:
    """The acceptance core: feed some batches, kill (drop the feeder and its
    memory), restart on the same row, drain to completion — the total flip
    count equals the slice sum exactly (a double-fed batch would show up as
    flipped-then-re-flipped: the second pass returns skipped, inflating
    jobs_skipped, or worse re-queuing already-running jobs)."""
    ws = _workspace(job_db, "feeder-resume")
    ids = _seed_failed_jobs(job_db, ws, 8, "RES")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=3)
    campaign_id = campaign["id"]

    first = _make_feeder(job_db, settings)
    _run_ticks(first, 2)
    row = job_db.get_campaign(campaign_id)
    assert 1 <= row["batches_submitted"] <= 2
    mid_flips = _queued_count(job_db, ws)
    assert mid_flips == row["jobs_succeeded"] == 3 * row["batches_submitted"]

    # Kill: a brand-new feeder (fresh memory — round-robin, backoff, pacing
    # all reset) reads the same row and continues from the stored cursor.
    second = _make_feeder(job_db, settings)
    _run_ticks(second, 5)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 8  # every job flipped back exactly once
    assert row["jobs_skipped"] == 0  # no re-fed batch, no double pass
    assert row["jobs_failed"] == 0
    assert row["batches_submitted"] * 3 >= 8  # ceil(8/3) slices fed
    assert _queued_count(job_db, ws) == 8  # flips == slice sum


def test_restart_refed_slice_absorbed_as_skips(job_db, settings) -> None:
    """The crash window between 'batch committed' and 'cursor advanced': the
    restart re-feeds the same slice and the write-path re-guards absorb it
    — a queued job with a pending (not failed/running) target node commits
    AGAIN but lands in the same idempotent state (the node is already
    pending, the job already queued), so the flip count stays exactly the
    slice size and zero jobs double-queue. The counter split records the
    first pass as succeeded and the replay as re-succeeded (the write
    guard's contract: a re-mark of an already-pending node is not busy)."""
    ws = _workspace(job_db, "feeder-crash-window")
    ids = _seed_failed_jobs(job_db, ws, 4, "CW")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=4)
    campaign_id = campaign["id"]

    first = _make_feeder(job_db, settings)
    # Feed the batch but never advance the cursor (the crash): the row
    # still holds offset=0 while the jobs are already flipped.
    outcome = first._submit_batch(dict(campaign))
    assert outcome.succeeded == 4
    assert job_db.get_campaign(campaign_id)["progress"]["offset"] == 0

    # Restart: the same slice is re-fed from the stored cursor.
    second = _make_feeder(job_db, settings)
    second._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    # The crashed pass's flips were never counted (its advance crashed);
    # the replay pass commits the same idempotent marks and its advance
    # lands: counters reflect one pass, and the workspace's queued count
    # proves the replay did not double-queue anything.
    assert row["jobs_succeeded"] == 4
    assert row["jobs_skipped"] == 0
    assert _queued_count(job_db, ws) == 4  # each job queued exactly once


# ---------------------------------------------------------------------------
# Paused workspace suspension
# ---------------------------------------------------------------------------


def test_paused_workspace_suspends_campaign_without_status_change(job_db, settings) -> None:
    """A paused workspace suspends feeding but never rewrites the campaign's
    own status (design §2.4 / CAMPAIGN-STATE-001): a not-yet-picked-up
    campaign stays pending, an already-running one stays running, counters
    stop, and the operator's workspace resume restarts the feed. The feeder
    neither flips the row to paused nor resumes the workspace itself."""
    control = WorkspaceWorkerControl(db_path=job_db)
    ws = _workspace(job_db, "feeder-ws-pause")
    ids = _seed_failed_jobs(job_db, ws, 12, "WP")
    pending = _create_rerun_campaign(job_db, ws, job_ids=ids[:2], watermark=100, batch_size=2)
    running = _create_rerun_campaign(job_db, ws, job_ids=ids[2:12], watermark=100, batch_size=2)
    feeder = _make_feeder(job_db, settings, control=control)

    control.pause(ws)
    _run_ticks(feeder, 3)
    row = job_db.get_campaign(pending["id"])
    assert row["status"] == "pending"  # never picked up while paused
    assert row["batches_submitted"] == 0

    # Unpause just long enough for the running campaign to start draining
    # (10 jobs / batch 2 = 5 batches; 3 ticks drain at most 3), then
    # re-pause: the mid-flight row must stay running.
    control.resume(ws)
    _run_ticks(feeder, 3)
    row = job_db.get_campaign(running["id"])
    assert 1 <= row["batches_submitted"] <= 3
    control.pause(ws)
    _run_ticks(feeder, 3)
    row = job_db.get_campaign(running["id"])
    assert row["status"] == "running"  # NOT paused — the campaign's own state
    frozen_batches = row["batches_submitted"]
    frozen_queued = _queued_count(job_db, ws)
    assert 1 <= frozen_batches < 5

    _run_ticks(feeder, 5)  # still paused: nothing moves
    row = job_db.get_campaign(running["id"])
    assert row["status"] == "running"
    assert row["batches_submitted"] == frozen_batches
    assert _queued_count(job_db, ws) == frozen_queued  # nothing fed while paused

    control.resume(ws)
    _run_ticks(feeder, 8)
    row = job_db.get_campaign(pending["id"])
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 2
    row = job_db.get_campaign(running["id"])
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 10


# ---------------------------------------------------------------------------
# Failure routing: transient backoff vs deterministic failure
# ---------------------------------------------------------------------------


def test_transient_failure_backs_off_linearly(job_db, feeder) -> None:
    """A transient error (DB connection loss family) backs the campaign off
    linearly: min(5×attempt, 60s), consecutive_failures rides progress_json,
    and the campaign never flips to failed."""
    ws = _workspace(job_db, "feeder-backoff")
    ids = _seed_failed_jobs(job_db, ws, 2, "BO")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
    campaign_id = campaign["id"]
    calls = {"count": 0}

    def _exploding_submit(camp):
        calls["count"] += 1
        raise OSError("connection reset by peer")

    feeder._submit_batch = _exploding_submit
    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "running"  # transient: never failed
    assert row["progress"]["consecutive_failures"] == 1
    assert feeder._attempts[campaign_id] == 1
    deadline = feeder._next_feed_at[campaign_id]
    assert 4.5 <= deadline - time.monotonic() <= 5.5

    # The backoff window gates the next tick...
    feeder._tick()
    assert calls["count"] == 1
    # ...and expires (simulated): the second failure doubles the wait.
    feeder._next_feed_at.clear()
    feeder._tick()
    assert calls["count"] == 2
    assert feeder._attempts[campaign_id] == 2
    row = job_db.get_campaign(campaign_id)
    assert row["progress"]["consecutive_failures"] == 2
    assert feeder._next_feed_at[campaign_id] - time.monotonic() >= 9.0


def test_backoff_caps_at_sixty_seconds(feeder) -> None:
    """The linear backoff saturates: attempt 20 waits 60s, not 100s."""
    feeder._attempts["cap-test"] = 20
    feeder._note_transient_failure(
        {
            "id": "cap-test",
            "progress": {},
            "batches_submitted": 0,
            "jobs_succeeded": 0,
            "jobs_skipped": 0,
            "jobs_failed": 0,
        }
    )
    assert feeder._next_feed_at["cap-test"] - time.monotonic() <= 60.5


def test_deterministic_failure_fails_campaign(job_db, feeder) -> None:
    """A JobServiceError while feeding is deterministic: the campaign flips
    to failed with the sample error, no backoff is scheduled, and the next
    tick does not touch it."""
    ws = _workspace(job_db, "feeder-deterministic")
    ids = _seed_failed_jobs(job_db, ws, 2, "DET")
    campaign = _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
    campaign_id = campaign["id"]

    def _broken_submit(camp):
        from server.app.services.job_errors import NotFoundError

        raise NotFoundError("Workflow revision vanished")

    feeder._submit_batch = _broken_submit
    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "failed"
    assert "Workflow revision vanished" in row["error_message"]
    assert row["finished_at"] is not None
    assert campaign_id not in feeder._attempts
    assert campaign_id not in feeder._next_feed_at

    feeder._tick()  # terminal: the active scan no longer matches
    assert job_db.get_campaign(campaign_id)["batches_submitted"] == 0


def test_failure_attribution_targets_the_picked_campaign(job_db, feeder) -> None:
    """PR-B review P1 回归锁：同 workspace 双 campaign 时，投递失败的归因
    （deterministic 翻 failed / transient 退避计数）必须落在 round-robin
    选中的那个 campaign 上——修复前异常块引用分组循环变量，指向组内
    排序最后的行：健康的 campaign 被错翻 failed，真正失败的无退避高频
    重试。campaign id 是随机 uuid，选中者由扫描序决定而非创建序，故用
    记录式 stub 断言「被投递者==被失败者」。"""
    ws = _workspace(job_db, "feeder-attribution")
    ids = _seed_failed_jobs(job_db, ws, 2, "ATTR")
    campaigns = [
        _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
        for _ in range(2)
    ]
    by_id = {c["id"]: c for c in campaigns}
    fed: list[str] = []

    def _broken_submit(camp):
        fed.append(camp["id"])
        from server.app.services.job_errors import NotFoundError

        raise NotFoundError("target vanished on the picked campaign")

    feeder._submit_batch = _broken_submit
    feeder._tick()

    # One campaign was picked and fed; the deterministic failure flipped it.
    assert len(fed) == 1
    picked_id = fed[0]
    assert job_db.get_campaign(picked_id)["status"] == "failed"
    assert (
        "target vanished on the picked campaign" in job_db.get_campaign(picked_id)["error_message"]
    )
    # The untouched sibling stays exactly where it was (never picked up).
    sibling_id = next(cid for cid in by_id if cid != picked_id)
    assert job_db.get_campaign(sibling_id)["status"] == "pending"
    assert job_db.get_campaign(sibling_id)["error_message"] == ""


def test_transient_attribution_targets_the_picked_campaign(job_db, feeder) -> None:
    """同上，瞬态路径：退避计数（内存态 _attempts/_next_feed_at 与落库的
    consecutive_failures）必须 key 到被投递的 campaign 上。"""
    ws = _workspace(job_db, "feeder-transient-attribution")
    ids = _seed_failed_jobs(job_db, ws, 2, "TATTR")
    campaigns = [
        _create_rerun_campaign(job_db, ws, job_ids=ids, watermark=100, batch_size=10)
        for _ in range(2)
    ]
    by_id = {c["id"]: c for c in campaigns}
    fed: list[str] = []

    def _exploding_submit(camp):
        fed.append(camp["id"])
        raise OSError("connection reset on the picked campaign")

    feeder._submit_batch = _exploding_submit
    feeder._tick()

    assert len(fed) == 1
    picked_id = fed[0]
    sibling_id = next(cid for cid in by_id if cid != picked_id)
    assert picked_id in feeder._attempts
    assert sibling_id not in feeder._attempts
    assert picked_id in feeder._next_feed_at
    assert sibling_id not in feeder._next_feed_at
    row = job_db.get_campaign(picked_id)
    assert row["progress"]["consecutive_failures"] == 1
    sibling = job_db.get_campaign(sibling_id)
    assert "consecutive_failures" not in sibling["progress"]


def test_corrupt_target_spec_fails_deterministically(job_db, feeder) -> None:
    """A corrupt stored target spec (a non-dict filter payload) is routed to
    failed, not backoff — retrying cannot fix a broken row."""
    ws = _workspace(job_db, "feeder-corrupt")
    _seed_failed_jobs(job_db, ws, 2, "COR")
    campaign = job_db.create_campaign(
        ws,
        "rerun",
        {"filter": "not-a-dict", "node_key": _NODE_KEYS[0]},
        watermark=1,
        batch_size=10,
        progress={"cursor": None, "processed": 0},
    )

    feeder._tick()
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "failed"
    assert "corrupt" in row["error_message"]


# ---------------------------------------------------------------------------
# Filter form: keyset slicing + the resume endpoint's wake
# ---------------------------------------------------------------------------


def test_filter_form_keyset_slicing_multi_batch(job_db, feeder) -> None:
    """Filter-form rerun drains in keyset slices across multiple ticks, every
    matching job flipped exactly once, newest-first order."""
    ws = _workspace(job_db, "feeder-filter")
    _seed_failed_jobs(job_db, ws, 7, "FLT")
    campaign = job_db.create_campaign(
        ws,
        "rerun",
        {"filter": {"status": "failed"}, "node_key": _NODE_KEYS[0]},
        watermark=100,
        batch_size=3,
        progress={"cursor": None, "processed": 0},
    )
    campaign_id = campaign["id"]

    _run_ticks(feeder, 4)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 7
    assert row["progress"]["processed"] == 7
    assert row["progress"]["cursor"] is None
    assert _queued_count(job_db, ws) == 7


def test_upgrade_mode_drains_with_upgrade_service(job_db, feeder) -> None:
    """Upgrade-mode campaigns drain through JobWorkflowUpgradeService.upgrade:
    each stale job is re-pinned and flipped to queued, already-current ones
    are skipped, and the watermark gate applies the same way."""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin import load_builtin_workflow

    ws = _workspace(job_db, "feeder-upgrade")
    definition = load_builtin_workflow("education_video_problems_generation")
    revisions = WorkflowRevisionService(job_db)
    original = revisions.publish_workspace_revision(ws, definition)
    current = revisions.publish_workspace_revision(ws, definition)
    assert current["id"] != original["id"]

    stale_ids: list[str] = []
    for i in range(3):
        job = job_db.create_job(
            workflow_key="education_video_problems_generation",
            source_type="question",
            source_id=f"UP{i}",
            run_id="",
            title=f"UP{i}",
            node_keys=_NODE_KEYS,
            workspace_id=ws,
            workflow_revision_id=original["id"],
            workflow_version=original["version"],
            workflow_definition_hash=original["definition_hash"],
            workflow_definition_snapshot_json=original["definition_json"],
        )
        job_db.update_job_status(job["id"], "completed")
        stale_ids.append(str(job["id"]))
    fresh = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="UPFRESH",
        run_id="",
        title="UPFRESH",
        node_keys=_NODE_KEYS,
        workspace_id=ws,
        workflow_revision_id=current["id"],
        workflow_version=current["version"],
        workflow_definition_hash=current["definition_hash"],
        workflow_definition_snapshot_json=current["definition_json"],
    )
    job_db.update_job_status(fresh["id"], "completed")

    campaign = job_db.create_campaign(
        ws,
        "upgrade",
        {"filter": {"status": "completed"}},
        watermark=100,
        batch_size=10,
        progress={"cursor": None, "processed": 0},
    )
    _run_ticks(feeder, 2)
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 3  # the stale ones re-pinned
    assert row["jobs_skipped"] == 1  # the already-current one
    assert _queued_count(job_db, ws) == 3
    for job_id in stale_ids:
        upgraded = job_db.get_job(job_id)
        assert upgraded["workflow_revision_id"] == current["id"]
        assert upgraded["status"] == "queued"


def test_resume_endpoint_wakes_feeder(client, job_db) -> None:
    """The resume route pokes app.state.campaign_feeder: pause → resume over
    the API flips the row back to running and wakes the feeder's event."""
    response = client.post("/api/workspaces", json={"id": "feeder-wake-ws", "name": "Feeder Wake"})
    assert response.status_code == 200, response.text
    ws = "feeder-wake-ws"
    ids = _seed_failed_jobs(job_db, ws, 2, "WK")
    create = client.post(
        f"/api/workspaces/{ws}/campaigns",
        json={
            "mode": "rerun",
            "rerun": {"job_ids": ids, "node_key": _NODE_KEYS[0], "watermark": 100},
        },
    )
    assert create.status_code == 200, create.text
    campaign_id = create.json()["campaign"]["id"]
    assert client.post(f"/api/workspaces/{ws}/campaigns/{campaign_id}/pause").status_code == 200
    # The feeder object is app.state; swap in a spy to observe the wake.
    app = client.app
    real_feeder = app.state.campaign_feeder
    woken = {"count": 0}
    real_feeder.wake = lambda: woken.__setitem__("count", woken["count"] + 1)  # type: ignore[method-assign]
    try:
        response = client.post(f"/api/workspaces/{ws}/campaigns/{campaign_id}/resume")
        assert response.status_code == 200, response.text
        assert response.json()["campaign"]["status"] == "running"
        assert woken["count"] == 1
    finally:
        del real_feeder.wake  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Smoke: a many-job rerun campaign drains cleanly
# ---------------------------------------------------------------------------


def test_large_rerun_campaign_smoke(job_db, settings) -> None:
    """The #532 acceptance smoke, CI-scaled: 600 failed jobs (2 batches of
    300), kill the feeder midway, restart, drain — every job flipped exactly
    once, counters consistent, completed. (10^5 is the staging calibration;
    the pass-time invariant it protects — no slow-pass — is owned by the
    scheduler's own tests; here we pin the drain correctness the feeder
    adds.)"""
    ws = _workspace(job_db, "feeder-smoke")
    _seed_failed_jobs(job_db, ws, _SMOKE_JOB_COUNT, "SMK")
    campaign = job_db.create_campaign(
        ws,
        "rerun",
        {"filter": {"status": "failed"}, "node_key": _NODE_KEYS[0]},
        watermark=_SMOKE_JOB_COUNT + 10,  # gate wide open
        batch_size=_SMOKE_BATCH_SIZE,
        progress={"cursor": None, "processed": 0},
    )
    campaign_id = campaign["id"]

    first = _make_feeder(job_db, settings)
    _run_ticks(first, 1)
    row = job_db.get_campaign(campaign_id)
    assert row["batches_submitted"] == 1
    assert row["jobs_succeeded"] == _SMOKE_BATCH_SIZE

    second = _make_feeder(job_db, settings)  # the "restart"
    _run_ticks(second, 3)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == _SMOKE_JOB_COUNT
    assert row["jobs_skipped"] == 0
    assert row["progress"]["processed"] == _SMOKE_JOB_COUNT
    assert _queued_count(job_db, ws) == _SMOKE_JOB_COUNT
    assert len(row["progress"]["watermark_samples"]) >= 2  # trail recorded
