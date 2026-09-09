"""Submit-mode campaign tests (#532 PR-C, design §1.5 / §2.3).

The full submit chain against real Postgres: campaign creation (inline
items and the bucket manifest channel), the feeder's manifest-cache slicing
through RunService.create_run (runs.campaign_id / created_by linkage), the
all-duplicates InvalidOperationError absorb, crash-replay zero duplication,
cache eviction on terminal/pause, the detail run-overview aggregation, and
the 5k-item batch latency baseline pin (#467-A's 6.9s must not regress
through the campaign path — create_run is the same chunked path, only the
caller changed).
"""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.campaign_service import CampaignService, campaign_manifest_key
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.run_service import RunService
from server.app.workflow_worker.campaign_feeder import CampaignFeeder
from tests.helpers import publish_builtin_revision
from tests.postgres_support import TEST_DATABASE_URL

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]

# The #467-A chunked-submit baseline is 6.9s per 5k items (the very number
# that forced the feeder OFF the poll loop, design §2.1). The campaign path
# rides the exact same create_run — this pin's job is to catch accidental
# O(n²) regressions (per-item round trips sneaking back), not to guard the
# absolute number. See _CAMPAIGN_VS_DIRECT_MAX_RATIO for why the assertion
# is relative (same-run direct-path control), not absolute.
_LATENCY_ITEM_COUNT = 5_000

# The campaign branch may cost at most this multiple of the direct create_run
# path measured in the same test run (same DB, same load): the submit branch
# adds a manifest slice + kwargs + a run-record read — linear bookkeeping, not
# a second copy of the chunked write path. An O(n²) or per-item round-trip
# regression in the branch inflates past this regardless of host speed; the
# absolute wall-clock (8s-166s observed under load average 8→40+) cancels out.
_CAMPAIGN_VS_DIRECT_MAX_RATIO = 3.0


class FakeObjectStorage:
    """ObjectStorage test double: put_object captures, open_stream replays."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.open_count = 0

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        self.objects[storage_key] = data

    def open_stream(self, storage_key: str):
        self.open_count += 1
        return io.BytesIO(self.objects[storage_key])


class _TrackingStream(io.BytesIO):
    """Records the read-size contract; exposes the position after each read.

    有界读回归锁的探针：feeder 必须以 manifest_max_bytes+1 为上限调用
    read(size)，且超限对象不允许被读完（position 停在上限处，缓冲区仍有
    剩余）——无界 .read() 会先在这里现形（size 为 -1/None）。
    """

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_sizes: list[int] = []
        self.position_after_read: int | None = None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        data = super().read(size)
        self.position_after_read = self.tell()
        return data


class TrackingObjectStorage(FakeObjectStorage):
    """open_stream replays through _TrackingStream so tests can pin the bound."""

    def __init__(self) -> None:
        super().__init__()
        self.streams: list[_TrackingStream] = []

    def open_stream(self, storage_key: str) -> _TrackingStream:
        self.open_count += 1
        stream = _TrackingStream(self.objects[storage_key])
        self.streams.append(stream)
        return stream


@pytest.fixture
def job_db(tmp_path: Path) -> JobQueries:
    return JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")


@pytest.fixture
def storage() -> FakeObjectStorage:
    return FakeObjectStorage()


def _make_service(job_db: JobQueries, settings: Any, storage: FakeObjectStorage) -> CampaignService:
    rerun = JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
    )
    return CampaignService(job_db, settings, rerun_service=rerun, object_storage=storage)


def _make_feeder(
    job_db: JobQueries, settings: Any, storage: FakeObjectStorage | None = None
) -> CampaignFeeder:
    leases = ExecutorLeaseRepository(job_db, data_dir=settings.data_dir)
    return CampaignFeeder(
        job_db,
        settings,
        rerun_service=JobRerunService(job_db, leases, settings),
        upgrade_service=JobWorkflowUpgradeService(job_db, leases),
        run_service=RunService(job_db, settings),
        object_storage=storage,
    )


def _workspace(job_db: JobQueries, name: str) -> str:
    workspace = job_db.create_workspace(
        name, default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, str(workspace["id"]))
    return str(workspace["id"])


def _insert_materials(job_db: JobQueries, workspace_id: str, count: int, prefix: str) -> None:
    with job_db.connect() as conn:
        for i in range(count):
            material_id = f"{prefix}-{i}"
            conn.execute(
                "insert into materials(id, workspace_id, content_hash, filename, content_type,"
                " size_bytes, storage_key, status, created_by)"
                " values (%s, %s, %s, %s, 'text/plain', 10, %s, 'ready', 'tester')"
                " on conflict (id) do nothing",
                (
                    material_id,
                    workspace_id,
                    f"hash-{material_id}",
                    f"{material_id}.txt",
                    f"{workspace_id}/hash-{material_id}/{material_id}.txt",
                ),
            )


def _material_items(count: int, prefix: str, *, offset: int = 0) -> list[dict[str, Any]]:
    return [
        {"type": "material", "material_id": f"{prefix}-{i}"} for i in range(offset, offset + count)
    ]


def _create_submit_campaign(
    service: CampaignService,
    workspace_id: str,
    items: list[dict[str, Any]],
    *,
    watermark: int = 1_000_000,
    batch_size: int = 5000,
    created_by: str = "op-campaign",
) -> dict[str, Any]:
    return service.create_campaign(
        workspace_id,
        "submit",
        items=items,
        watermark=watermark,
        batch_size=batch_size,
        created_by=created_by,
    )


def _run_ticks(feeder: CampaignFeeder, rounds: int) -> None:
    """Drive the loop synchronously; clear per-tick pacing between rounds."""
    for _ in range(rounds):
        feeder._tick()
        feeder._next_feed_at.clear()


def _campaign_job_count(job_db: JobQueries, campaign_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute(
            "select count(*) as n from jobs j join runs r on r.id=j.run_id where r.campaign_id=%s",
            (campaign_id,),
        ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# Full chain: create (inline) → feed → runs linked → completed
# ---------------------------------------------------------------------------


def test_submit_inline_campaign_full_chain(job_db, settings, storage) -> None:
    ws = _workspace(job_db, "submit-inline")
    _insert_materials(job_db, ws, 5, "IN")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(5, "IN"), batch_size=3, watermark=100
    )
    campaign_id = campaign["id"]
    assert campaign["status"] == "pending"
    assert campaign["progress"] == {"item_offset": 0}

    feeder = _make_feeder(job_db, settings, storage)
    _run_ticks(feeder, 1)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "running"  # first batch (3 items) in, one more to go
    assert row["batches_submitted"] == 1
    assert row["jobs_succeeded"] == 3
    assert row["progress"]["item_offset"] == 3

    _run_ticks(feeder, 1)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["batches_submitted"] == 2
    assert row["jobs_succeeded"] == 5  # 3 + 2
    assert row["progress"]["item_offset"] == 6  # advanced past the short tail slice

    # The runs carry the campaign linkage (v80 column) and created_by.
    with job_db.connect() as conn:
        runs = conn.execute(
            "select id, campaign_id, created_by from runs where campaign_id=%s order by created_at",
            (campaign_id,),
        ).fetchall()
    assert len(runs) == 2
    assert {str(r["campaign_id"]) for r in runs} == {campaign_id}
    assert {str(r["created_by"]) for r in runs} == {"op-campaign"}
    assert _campaign_job_count(job_db, campaign_id) == 5

    # Detail aggregation: each run shows its live job counts. (Runs created
    # in the same transaction-second tie on created_at, so assert the
    # multiset — the ordering is newest-first only across distinct stamps.)
    detail = service.get_campaign(ws, campaign_id)
    assert sorted(run["job_count"] for run in detail["runs"]) == [2, 3]
    assert all(run["id"] in {str(r["id"]) for r in runs} for run in detail["runs"])


def test_submit_bucket_manifest_campaign_full_chain(job_db, settings, storage) -> None:
    """The spill channel: items beyond manifest_inline_max_bytes land in the
    bucket under {workspace}/campaigns/{id}/manifest.jsonl; the feeder reads
    that object ONCE (open_stream), caches it in-process, and slices it
    exactly like the inline form. (The inline ceiling is lowered per-test:
    262144 bytes ≈ 2-3k real-world items — materializing that many rows to
    cross it would price this test at the 5k baseline's cost for no added
    coverage of the channel decision, which is test_campaign_service's.)"""
    ws = _workspace(job_db, "submit-bucket")
    count = 60
    _insert_materials(job_db, ws, count, "BK")
    settings.executor_runtime.campaigns.manifest_inline_max_bytes = 1024
    items = _material_items(count, "BK")

    service = _make_service(job_db, settings, storage)
    campaign = service.create_campaign(
        ws, "submit", items=items, watermark=1_000_000, batch_size=25, created_by="op-bucket"
    )
    assert "items" not in campaign["target_spec"]
    key = campaign_manifest_key(ws, campaign["id"])
    assert campaign["target_spec"]["manifest_storage_key"] == key
    assert campaign["target_spec"]["manifest_item_count"] == count
    assert key in storage.objects

    feeder = _make_feeder(job_db, settings, storage)
    _run_ticks(feeder, 3)  # 60 / 25 → ceil = 3 batches
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "completed"
    assert row["batches_submitted"] == 3
    assert row["jobs_succeeded"] == count
    assert row["progress"]["item_offset"] == 75  # 3 × batch_size past the end
    assert storage.open_count == 1  # loaded once, cached across the batches
    assert feeder._manifest_cache[campaign["id"]][0] == {"type": "material", "material_id": "BK-0"}
    assert _campaign_job_count(job_db, campaign["id"]) == count


# ---------------------------------------------------------------------------
# Crash replay: zero duplicate jobs; the re-fed batch is absorbed
# ---------------------------------------------------------------------------


def test_crash_replay_creates_no_duplicate_jobs(job_db, settings, storage) -> None:
    """The acceptance core: feed the batch, crash BEFORE the cursor advance
    (the stored item_offset still points at the fed slice), restart on a
    fresh feeder — the re-fed slice resolves to the same deterministic run
    id, dedup drops every existing key, and the absorb advances the cursor
    with created 0 (the #531 CLI's message-string hack resolved to a type
    catch). No second run row, no duplicate jobs."""
    ws = _workspace(job_db, "submit-crash")
    _insert_materials(job_db, ws, 4, "CR")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(4, "CR"), batch_size=4, watermark=100
    )
    campaign_id = campaign["id"]

    first = _make_feeder(job_db, settings, storage)
    # Feed the batch but never advance the cursor (the crash window).
    outcome = first._submit_batch(dict(campaign))
    assert outcome.succeeded == 4
    assert outcome.exhausted
    assert job_db.get_campaign(campaign_id)["progress"]["item_offset"] == 0
    assert _campaign_job_count(job_db, campaign_id) == 4

    # Restart: a brand-new feeder re-feeds the SAME slice from the stored cursor.
    second = _make_feeder(job_db, settings, storage)
    _run_ticks(second, 1)
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["batches_submitted"] == 1  # the crashed pass never advanced
    assert row["jobs_succeeded"] == 0  # the replay created nothing
    assert row["jobs_skipped"] == 4  # the absorb counted the duplicates
    assert row["progress"]["item_offset"] == 4
    # Exactly one run row, exactly four jobs: the replay duplicated nothing.
    assert _campaign_job_count(job_db, campaign_id) == 4
    with job_db.connect() as conn:
        n = conn.execute(
            "select count(*) as n from runs where campaign_id=%s", (campaign_id,)
        ).fetchone()
    assert int(n["n"]) == 1


def test_absorb_after_manual_run_of_same_items(job_db, settings, storage) -> None:
    """The complement channel (design §1.5): a manual /runs submission of the
    same items completes the batch's jobs under the same dedup key space.
    The campaign's batch is then fully absorbed — cursor advances,
    created 0 — and the deterministic run id means the manual run's row is
    the campaign's row (first writer owns the campaign_id stamp)."""
    ws = _workspace(job_db, "submit-manual")
    _insert_materials(job_db, ws, 3, "MN")
    items = _material_items(3, "MN")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(service, ws, items, batch_size=3, watermark=100)

    # The manual run wins the row: created by hand, no campaign stamp.
    manual = RunService(job_db, settings).create_run(ws, workflow_key=ws, items=items)
    assert manual["created_count"] == 3

    feeder = _make_feeder(job_db, settings, storage)
    _run_ticks(feeder, 1)
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 0  # nothing left to create
    assert row["jobs_skipped"] == 3
    # The manual run kept its identity (first writer); the campaign created
    # no second run over the same items.
    with job_db.connect() as conn:
        rows = conn.execute(
            "select campaign_id from runs where id=%s", (manual["run"]["id"],)
        ).fetchall()
    assert str(rows[0]["campaign_id"]) == ""  # unstamped by the manual writer


def test_already_succeeded_batch_replay_absorbed_with_created_zero(
    job_db, settings, storage
) -> None:
    """The absorb pinned at the unit level: after a successful feed, calling
    _submit_batch on the same un-advanced cursor again returns created 0 —
    succeeded 0 with the WHOLE slice counted as skipped (the P2-1 rule: a
    created_count below the slice size is the dedup-dropped remainder, and
    the #501 heal return folds into the same accounting) — without touching
    any row (the pure in-process form of the crash replay)."""
    ws = _workspace(job_db, "submit-absorb")
    _insert_materials(job_db, ws, 3, "AB")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(3, "AB"), batch_size=3, watermark=100
    )
    feeder = _make_feeder(job_db, settings, storage)

    first = feeder._submit_batch(dict(campaign))
    assert (first.succeeded, first.skipped, first.failed) == (3, 0, 0)
    assert first.exhausted

    # Same cursor, same slice: the re-feed heals the run row (created 0) and
    # the slice lands as skips.
    second = feeder._submit_batch(dict(campaign))
    assert (second.succeeded, second.skipped, second.failed) == (0, 3, 0)
    assert second.exhausted
    assert _campaign_job_count(job_db, campaign["id"]) == 3


def test_partial_dedup_batch_counts_skipped(job_db, settings, storage) -> None:
    """PR-C review P2-1 回归锁：slice 同时含新 item 与重复 item 时
    （[new, dup, new]），create_run 过滤重复后返回较小的 created_count，
    计数必须与之对齐——succeeded=2/skipped=1，item_offset 前进 3。
    修复前成功路径固定 skipped=0：[A, A] 会显示 succeeded=1/skipped=0
    而 offset 进 2，completed campaign 的计数与实际处理项数不符。"""
    ws = _workspace(job_db, "submit-mixed-dedup")
    _insert_materials(job_db, ws, 3, "MD")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(3, "MD"), batch_size=3, watermark=100
    )
    campaign_id = campaign["id"]

    # One item of the batch already has a job (a manual run won the dedup
    # key before the campaign's first feed).
    manual = RunService(job_db, settings).create_run(
        ws, workflow_key=ws, items=_material_items(1, "MD")
    )
    assert manual["created_count"] == 1

    feeder = _make_feeder(job_db, settings, storage)
    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 2  # the fresh items created
    assert row["jobs_skipped"] == 1  # the duplicate dropped by dedup
    assert row["progress"]["item_offset"] == 3  # the whole slice advanced
    assert _campaign_job_count(job_db, campaign_id) == 2  # manual 1 + campaign 2


# ---------------------------------------------------------------------------
# Manifest cache eviction
# ---------------------------------------------------------------------------


def test_manifest_cache_evicted_on_terminal_and_pause(job_db, settings, storage) -> None:
    """The cache holds only ACTIVE campaigns (design §2.2): completion evicts
    on the next tick's _prune_memory, and a pause — which leaves the active
    scan — evicts too, so a paused campaign's manifest is not pinned in
    memory (bounded by max_active_per_workspace in steady state)."""
    ws = _workspace(job_db, "submit-evict")
    _insert_materials(job_db, ws, 2, "EV")
    _insert_materials(job_db, ws, 6, "EVX")
    service = _make_service(job_db, settings, storage)
    done = _create_submit_campaign(
        service, ws, _material_items(2, "EV"), batch_size=2, watermark=100
    )
    # batch_size 1 over 6 items: still mid-drain after a few turns.
    paused = _create_submit_campaign(
        service, ws, _material_items(6, "EVX"), batch_size=1, watermark=100
    )

    feeder = _make_feeder(job_db, settings, storage)
    # One batch per workspace per tick (round-robin hands each campaign a
    # turn; the feed-interval pacing is cleared between rounds): three
    # rounds exhaust the done campaign while the paused one sits mid-drain.
    _run_ticks(feeder, 3)
    assert job_db.get_campaign(done["id"])["status"] == "completed"
    assert job_db.get_campaign(paused["id"])["status"] == "running"
    assert feeder._manifest_cache  # the loaded manifests live

    # Pause the second campaign mid-drain: it leaves the active set.
    service.pause_campaign(ws, paused["id"])
    feeder._tick()  # the prune runs on the (now active-only) scan
    assert done["id"] not in feeder._manifest_cache
    assert paused["id"] not in feeder._manifest_cache

    # Resume: the manifest reloads (the cache miss is a plain reload), the
    # remaining batches feed, and the completed row's cache entry leaves on
    # the NEXT tick's prune (the prune runs against the tick's scan
    # snapshot, so the completing tick itself still holds it).
    service.resume_campaign(ws, paused["id"])
    _run_ticks(feeder, 6)
    assert job_db.get_campaign(paused["id"])["status"] == "completed"
    feeder._tick()  # the prune against the now-terminal scan
    assert paused["id"] not in feeder._manifest_cache
    assert job_db.get_campaign(paused["id"])["jobs_succeeded"] == 6


def test_manifest_cache_evicts_over_byte_budget_and_reloads(job_db, settings, storage) -> None:
    """PR-C review P1 回归锁：manifest 缓存按「规范序列化字节数」全局记账，
    超过 campaigns.manifest_cache_max_bytes 时逐出最久未用（多 workspace 并发
    + 被水位阻塞的 running campaign 持续持有时，进程内存有全局上限）。逐出
    不是终态：被逐出的 campaign 下次投放按 item_offset 重装载（一次对象存储
    读），进度零丢失——与 pause→resume 的重装载路径同一条。"""
    from server.app.services.campaign_manifest import serialize_manifest

    ws_a = _workspace(job_db, "submit-cache-a")
    ws_b = _workspace(job_db, "submit-cache-b")
    _insert_materials(job_db, ws_a, 4, "MB-A")
    _insert_materials(job_db, ws_b, 4, "MB-B")
    settings.executor_runtime.campaigns.manifest_inline_max_bytes = 64  # bucket channel
    service = _make_service(job_db, settings, storage)
    a = _create_submit_campaign(
        service, ws_a, _material_items(4, "MB-A"), batch_size=4, watermark=100
    )
    b = _create_submit_campaign(
        service, ws_b, _material_items(4, "MB-B"), batch_size=4, watermark=100
    )
    bytes_a = len(serialize_manifest(_material_items(4, "MB-A")).encode("utf-8"))
    bytes_b = len(serialize_manifest(_material_items(4, "MB-B")).encode("utf-8"))
    assert bytes_a == bytes_b  # same shape, different dedup keys

    feeder = _make_feeder(job_db, settings, storage)
    # One byte below the two-manifest sum: loading the second must evict
    # the first (LRU), even though both campaigns are still ACTIVE.
    settings.executor_runtime.campaigns.manifest_cache_max_bytes = bytes_a + bytes_b - 1
    a_row, b_row = job_db.get_campaign(a["id"]), job_db.get_campaign(b["id"])

    assert feeder._load_manifest(dict(a_row)) == _material_items(4, "MB-A")
    assert a["id"] in feeder._manifest_cache
    assert feeder._manifest_cache_bytes[a["id"]] == bytes_a  # byte accounting
    opens_after_first_loads = storage.open_count

    assert feeder._load_manifest(dict(b_row)) == _material_items(4, "MB-B")
    assert b["id"] in feeder._manifest_cache
    assert a["id"] not in feeder._manifest_cache  # LRU-evicted over the budget
    assert a["id"] not in feeder._manifest_cache_bytes  # accounting evicted too

    # The evicted campaign is NOT terminal: the tick re-loads its manifest
    # from the object store at the stored item_offset and completes it with
    # zero progress lost (the same reload path as pause → resume).
    _run_ticks(feeder, 2)
    assert storage.open_count > opens_after_first_loads  # a real re-read
    row = job_db.get_campaign(a["id"])
    assert row["status"] == "completed"
    assert row["jobs_succeeded"] == 4
    assert row["progress"]["item_offset"] == 4
    assert _campaign_job_count(job_db, a["id"]) == 4
    assert job_db.get_campaign(b["id"])["status"] == "completed"


def test_corrupt_bucket_manifest_fails_deterministically(job_db, settings, storage) -> None:
    """A manifest object that no longer round-trips (operator damage, bucket
    truncation) is deterministic: failed row with the sample error, no
    backoff, no infinite retry."""
    ws = _workspace(job_db, "submit-corrupt")
    _insert_materials(job_db, ws, 2, "CO")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(2, "CO"), batch_size=2, watermark=100
    )
    # Force the bucket channel shape on a small manifest by hand.
    with job_db.connect() as conn:
        conn.execute(
            "update campaigns set target_spec_json=%s where id=%s",
            (
                '{"manifest_item_count": 2, "manifest_storage_key": "'
                + campaign_manifest_key(ws, campaign["id"])
                + '"}',
                campaign["id"],
            ),
        )
    storage.objects[campaign_manifest_key(ws, campaign["id"])] = b"not jsonl at all\n"

    feeder = _make_feeder(job_db, settings, storage)
    feeder._tick()
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "failed"
    assert "corrupt" in row["error_message"]
    assert campaign["id"] not in feeder._attempts


def test_non_utf8_bucket_manifest_fails_deterministically(job_db, settings, storage) -> None:
    """PR-C review P2-2 回归锁：manifest 对象被覆盖/损坏为非 UTF-8 字节时，
    decode("utf-8") 的 UnicodeDecodeError 必须与 ManifestError 同路——确定性
    failed（对象损坏重试无法修复），而不是落进瞬态异常族无限退避、campaign
    永远卡 running。修复前 except 只捕 ManifestError，decode 错误逃逸成
    transient。"""
    ws = _workspace(job_db, "submit-nonutf8")
    _insert_materials(job_db, ws, 2, "N8")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(2, "N8"), batch_size=2, watermark=100
    )
    key = campaign_manifest_key(ws, campaign["id"])
    with job_db.connect() as conn:
        conn.execute(
            "update campaigns set target_spec_json=%s where id=%s",
            (
                '{"manifest_item_count": 2, "manifest_storage_key": "' + key + '"}',
                campaign["id"],
            ),
        )
    storage.objects[key] = b"\xff\xfe\x00bad utf8\xff"

    feeder = _make_feeder(job_db, settings, storage)
    feeder._tick()
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "failed"  # deterministic, NOT transient backoff
    assert "corrupt" in row["error_message"]
    assert campaign["id"] not in feeder._attempts  # no backoff was scheduled
    assert campaign["id"] not in feeder._next_feed_at


def test_replaced_oversized_manifest_object_fails_with_bounded_read(
    job_db, settings, storage
) -> None:
    """PR #559 二轮回归锁：manifest 对象在 campaign 创建后被覆盖成超过
    manifest_max_bytes 的内容时，读取必须有界（至多 manifest_max_bytes+1 字节
    ——巨大对象不能在任何校验前把内存吃满），并按确定性损坏失败（failed +
    样例错误），而不是无界读或瞬态退避。恰好等于上限的对象在 +1 的区分语义
    下仍正常装载（limit+1 的读全量返回 limit 字节，不算超限）。"""
    from server.app.services.campaign_manifest import serialize_manifest

    tracking = TrackingObjectStorage()
    ws = _workspace(job_db, "submit-replaced")
    _insert_materials(job_db, ws, 2, "RP")
    items = _material_items(2, "RP")
    exact = serialize_manifest(items).encode("utf-8")
    # 上限压到恰好等于该 manifest 的序列化字节数：边界两侧行为一目了然。
    settings.executor_runtime.campaigns.manifest_max_bytes = len(exact)
    service = _make_service(job_db, settings, tracking)
    campaign = _create_submit_campaign(service, ws, items, batch_size=2, watermark=100)
    campaign_id = campaign["id"]
    key = campaign_manifest_key(ws, campaign_id)
    # 手工改成桶通道形态（inline 上限未动；对象恰好等于上限）。
    with job_db.connect() as conn:
        conn.execute(
            "update campaigns set target_spec_json=%s where id=%s",
            (
                '{"manifest_item_count": 2, "manifest_storage_key": "' + key + '"}',
                campaign_id,
            ),
        )
    tracking.objects[key] = exact

    feeder = _make_feeder(job_db, settings, tracking)
    # 恰好等于上限：limit+1 的有界读全量返回 limit 字节，解析照常。
    assert feeder._load_manifest(dict(job_db.get_campaign(campaign_id))) == items
    at_limit = tracking.streams[-1]
    assert at_limit.read_sizes == [len(exact) + 1]
    assert at_limit.position_after_read == len(exact)

    # 对象被覆盖成超限内容：读取停在 limit+1 处（未读完），确定性 failed。
    oversized = b"x" * (len(exact) + 10)
    tracking.objects[key] = oversized
    feeder._manifest_cache.clear()
    feeder._manifest_cache_bytes.clear()
    feeder._tick()
    row = job_db.get_campaign(campaign_id)
    assert row["status"] == "failed"
    assert "corrupt" in row["error_message"]
    assert "exceeds the" in row["error_message"]
    assert str(len(exact)) in row["error_message"]
    assert campaign_id not in feeder._attempts  # 确定性失败，不进退避
    over = tracking.streams[-1]
    assert over.read_sizes == [len(exact) + 1]  # 有界调用，而非无界 read()
    assert over.position_after_read == len(exact) + 1  # 停在上限处
    assert len(oversized) > len(exact) + 1  # 缓冲区仍有未读字节：没被读完
    assert _campaign_job_count(job_db, campaign_id) == 0  # 什么都没建


# ---------------------------------------------------------------------------
# Detail aggregation
# ---------------------------------------------------------------------------


def test_detail_run_overview_tracks_runs_and_counts(job_db, settings, storage) -> None:
    """The aggregate reads live run status/counts (run_job_status_counts),
    newest first, and rerun-mode campaigns carry an empty list."""
    ws = _workspace(job_db, "submit-detail")
    _insert_materials(job_db, ws, 4, "DT")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(4, "DT"), batch_size=2, watermark=100
    )

    feeder = _make_feeder(job_db, settings, storage)
    _run_ticks(feeder, 2)
    assert job_db.get_campaign(campaign["id"])["status"] == "completed"

    detail = service.get_campaign(ws, campaign["id"])
    assert len(detail["runs"]) == 2
    assert all(run["job_count"] == 2 for run in detail["runs"])
    assert all(run["created_count"] == 2 for run in detail["runs"])
    assert all(run["status"] in ("created", "queued") for run in detail["runs"])

    # A rerun campaign never aggregates runs.
    failed_job = job_db.create_job(
        workflow_key=ws,
        source_type="question",
        source_id="DTQ",
        run_id="",
        title="DTQ",
        node_keys=_NODE_KEYS,
        workspace_id=ws,
    )
    job_db.update_job_status(failed_job["id"], "failed", "boom")
    rerun = service.create_campaign(
        ws, "rerun", job_ids=[str(failed_job["id"])], node_key=_NODE_KEYS[0], watermark=100
    )
    assert service.get_campaign(ws, rerun["id"])["runs"] == []


def test_detail_run_overview_returns_latest_fifty_runs_only(job_db, settings, storage) -> None:
    """PR #559 二轮回归锁：详情聚合先截取最新 50 条 run 再聚合——55 条历史
    批次中恰好返回最新 50 条（最旧 5 条出局）、顺序 created_at desc、每条的
    job_count 按行取自计数表（cnt<>0 过滤 + 无计数行的 coalesce 0）。种子走
    直插 run 行：本测只钉查询分页/聚合的正确性，不重放整条 feeder 链。"""
    ws = str(
        job_db.create_workspace(
            "submit-overview-cap", default_workflow_key="education_video_problems_generation"
        )["id"]
    )
    campaign = job_db.create_campaign(
        ws, "submit", {"items": []}, watermark=100, batch_size=10, progress={"item_offset": 0}
    )
    campaign_id = campaign["id"]
    # 55 条 run，created_at 逐秒递增（时间戳互异，排序无并列歧义）。
    base = datetime.now(UTC) - timedelta(seconds=60)
    with job_db.connect() as conn:
        for i in range(55):
            conn.execute(
                "insert into runs(id, workspace_id, status, created_count, campaign_id,"
                " created_at) values (%s, %s, 'created', 2, %s, %s)",
                (f"{campaign_id}-run-{i:02d}", ws, campaign_id, base + timedelta(seconds=i)),
            )
            if i == 7:
                continue  # run-07 不播种计数行：left join + coalesce 的 0 路径
            # 每行计数互异（证明按行聚合而非全局求和）；failed 的 0 计数行
            # 必须被 cnt<>0 过滤掉。
            conn.execute(
                "insert into run_job_status_counts(run_id, status, cnt)"
                " values (%s, 'completed', %s), (%s, 'pending', 2), (%s, 'failed', 0)",
                (
                    f"{campaign_id}-run-{i:02d}",
                    i % 3,
                    f"{campaign_id}-run-{i:02d}",
                    f"{campaign_id}-run-{i:02d}",
                ),
            )
    service = _make_service(job_db, settings, storage)
    detail = service.get_campaign(ws, campaign_id)
    # 恰好 50 条、最新在前、最旧 5 条（run-00..04）出局。
    assert [run["id"] for run in detail["runs"]] == [
        f"{campaign_id}-run-{i:02d}" for i in range(54, 4, -1)
    ]
    by_id = {run["id"]: run for run in detail["runs"]}
    for i in range(54, 4, -1):
        run = by_id[f"{campaign_id}-run-{i:02d}"]
        assert run["job_count"] == (0 if i == 7 else (i % 3) + 2)
        assert run["created_count"] == 2
        assert run["status"] == "created"


def test_rerun_mode_still_works_alongside_submit(job_db, settings, storage) -> None:
    """A rerun campaign and a submit campaign in the same workspace drain
    through the same feeder without interference (the dispatch stays clean
    when the submit branch is present)."""
    ws = _workspace(job_db, "submit-mixed")
    _insert_materials(job_db, ws, 3, "MX")
    failed_job = job_db.create_job(
        workflow_key=ws,
        source_type="question",
        source_id="MXQ",
        run_id="",
        title="MXQ",
        node_keys=_NODE_KEYS,
        workspace_id=ws,
    )
    job_db.update_job_status(failed_job["id"], "failed", "boom")
    service = _make_service(job_db, settings, storage)
    rerun = service.create_campaign(
        ws, "rerun", job_ids=[str(failed_job["id"])], node_key=_NODE_KEYS[0], watermark=100
    )
    submit = _create_submit_campaign(
        service, ws, _material_items(3, "MX"), batch_size=2, watermark=100
    )

    feeder = _make_feeder(job_db, settings, storage)
    _run_ticks(feeder, 4)
    assert job_db.get_campaign(rerun["id"])["status"] == "completed"
    assert job_db.get_campaign(submit["id"])["status"] == "completed"
    assert job_db.get_campaign(submit["id"])["jobs_succeeded"] == 3


# ---------------------------------------------------------------------------
# Latency baseline pin (#467-A: 5k items / 6.9s must not regress)
# ---------------------------------------------------------------------------


def test_five_thousand_item_batch_latency_baseline(job_db, settings, storage) -> None:
    """The #467-A pin through the campaign path, as a RELATIVE assertion:
    the campaign branch's cost over the direct create_run path must stay a
    small multiple. An absolute ceiling (the first draft used 30s against
    the 6.9s staging number) is not runnable on a shared dev machine —
    observed spread ran 8s → 166s purely from co-located load (load avg 40+
    with parallel gates/npm installs on the same host), while the direct
    path slowed identically. Comparing both paths inside the SAME test
    eliminates the machine factor: what must not regress is the submit
    branch's own overhead (manifest slice + kwargs + event stamps), which
    an O(n²) or per-item round-trip regression would inflate well past
    the 3× ceiling regardless of host load."""
    ws = _workspace(job_db, "submit-latency")
    _insert_materials(job_db, ws, _LATENCY_ITEM_COUNT * 2, "LAT")
    service = _make_service(job_db, settings, storage)
    items = _material_items(_LATENCY_ITEM_COUNT, "LAT")
    campaign = _create_submit_campaign(service, ws, items, batch_size=5000, watermark=100)
    feeder = _make_feeder(job_db, settings, storage)

    # Control: the direct create_run over a DISJOINT item set (different
    # dedup keys), measured in the same run against the same database.
    control_items = _material_items(_LATENCY_ITEM_COUNT, "LAT", offset=_LATENCY_ITEM_COUNT)
    control_service = RunService(job_db, settings)
    control_started = time.perf_counter()
    control = control_service.create_run(ws, workflow_key=ws, items=control_items)
    control_elapsed = time.perf_counter() - control_started
    assert control["created_count"] == _LATENCY_ITEM_COUNT

    started = time.perf_counter()
    outcome = feeder._submit_batch(dict(campaign))
    elapsed = time.perf_counter() - started

    assert outcome.succeeded == _LATENCY_ITEM_COUNT
    assert _campaign_job_count(job_db, campaign["id"]) == _LATENCY_ITEM_COUNT
    assert elapsed < control_elapsed * _CAMPAIGN_VS_DIRECT_MAX_RATIO, (
        f"campaign batch {elapsed:.1f}s vs direct-path control {control_elapsed:.1f}s"
        f" (ratio {elapsed / control_elapsed:.1f}x > {_CAMPAIGN_VS_DIRECT_MAX_RATIO}x)"
        " — the submit branch's own overhead regressed, independent of host load"
    )


def test_state_drift_failure_is_not_absorbed(job_db, settings, storage) -> None:
    """PR-C review P2 回归锁：状态漂移的 InvalidOperationError（基类族——
    material 在 campaign 生命周期内被 TTL 清扫翻成 expired）必须走
    确定性 failed，而不是被吸收成 skipped 的「静默 completed」。
    修复前 except InvalidOperationError 连基类一起吞：campaign 显示
    completed 而 job 一个都没建、无 error_message 线索。"""
    ws = _workspace(job_db, "submit-drift")
    _insert_materials(job_db, ws, 2, "DR")
    service = _make_service(job_db, settings, storage)
    campaign = _create_submit_campaign(
        service, ws, _material_items(2, "DR"), batch_size=2, watermark=100
    )

    # 创建校验已过（materials 当时 ready）；投放前状态漂移：expired。
    with job_db.connect() as conn:
        conn.execute(
            "update materials set status='expired' where workspace_id=%s",
            (ws,),
        )

    feeder = _make_feeder(job_db, settings, storage)
    feeder._tick()
    row = job_db.get_campaign(campaign["id"])
    assert row["status"] == "failed", "state drift must fail the campaign"
    assert "not ready" in row["error_message"]
    assert row["jobs_succeeded"] == 0
    assert row["jobs_skipped"] == 0, "no silent-absorb accounting for drift"
    with job_db.connect() as conn:
        jobs = conn.execute(
            "select count(*) as n from jobs where workspace_id=%s", (ws,)
        ).fetchone()
    assert jobs["n"] == 0
