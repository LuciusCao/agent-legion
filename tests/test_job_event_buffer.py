import asyncio
import json

from server.app.job_events import JobEventBuffer, WorkspaceJobEventAggregator


def test_records_revisions_in_order():
    buffer = JobEventBuffer(max_events=10)

    first = buffer.record_job_updated("ws1", "job1")
    second = buffer.record_job_updated("ws1", "job2")

    assert first == 1
    assert second == 2
    events = buffer.drain()
    assert [event.revision for event in events] == [1, 2]
    assert [event.job_id for event in events] == ["job1", "job2"]


def test_deduplicates_workspace_job_to_latest_revision():
    buffer = JobEventBuffer(max_events=10)

    buffer.record_job_updated("ws1", "job1")
    latest = buffer.record_job_updated("ws1", "job1")

    events = buffer.drain_compacted()
    assert len(events.updated_job_ids_by_workspace["ws1"]) == 1
    assert events.updated_job_ids_by_workspace["ws1"] == {"job1"}
    assert events.latest_revision == latest


def test_marks_workspace_for_resync_when_buffer_overflows():
    buffer = JobEventBuffer(max_events=2)

    buffer.record_job_updated("ws1", "job1")
    buffer.record_job_updated("ws1", "job2")
    buffer.record_job_updated("ws1", "job3")

    drained = buffer.drain_compacted()
    assert drained.resync_workspace_ids == {"ws1"}
    assert drained.latest_revision == 3


class FakeJobQueries:
    def __init__(self):
        self.summary_calls = []
        self.stats_calls = []

    def list_patch_summaries(self, workspace_id, job_ids):
        self.summary_calls.append((workspace_id, list(job_ids)))
        return [{"id": job_id, "workspace_id": workspace_id} for job_id in job_ids]

    def count_jobs_by_status(self, workspace_id):
        self.stats_calls.append(workspace_id)
        return {"running": 1}


class FakeEventBus:
    def __init__(self):
        self.patch_batches = []
        self.resyncs = []
        self.dashboard_stats = []

    def publish(self, channel: str, payload: str) -> None:
        data = json.loads(payload)
        if data["type"] == "job_patch_batch":
            self.patch_batches.append(
                (
                    channel,
                    data["revision"],
                    data["stats"],
                    data["jobs"],
                    data["deleted_job_ids"],
                )
            )
        elif data["type"] == "resync_required":
            self.resyncs.append((channel, data["latest_revision"], data["reason"]))
        elif data["type"] == "workspace_stats_batch":
            self.dashboard_stats.append((channel, data["latest_revision"], data["workspaces"]))


def test_aggregator_flushes_compacted_patch_batch():
    buffer = JobEventBuffer(max_events=10)
    buffer.record_job_updated("ws1", "job1")
    buffer.record_job_updated("ws1", "job1")
    buffer.record_job_deleted("ws1", "job2")
    queries = FakeJobQueries()
    bus = FakeEventBus()
    aggregator = WorkspaceJobEventAggregator(buffer, queries, bus)

    aggregator.flush_once()

    assert bus.patch_batches == [
        (
            "workspace:ws1",
            3,
            {"running": 1},
            [{"id": "job1", "workspace_id": "ws1"}],
            ["job2"],
        )
    ]
    assert len(bus.dashboard_stats) == 1
    dashboard_channel, dashboard_revision, dashboard_workspaces = bus.dashboard_stats[0]
    assert dashboard_channel == "dashboard"
    assert dashboard_revision == 3
    assert [ws["id"] for ws in dashboard_workspaces] == ["ws1"]


def test_aggregator_flush_runs_off_event_loop():
    buffer = JobEventBuffer(max_events=10)
    buffer.record_job_updated("ws1", "job1")
    queries = FakeJobQueries()
    bus = FakeEventBus()
    aggregator = WorkspaceJobEventAggregator(buffer, queries, bus)

    async def _flush() -> None:
        await asyncio.to_thread(aggregator.flush_once)

    asyncio.run(_flush())

    assert len(bus.patch_batches) == 1
    assert bus.patch_batches[0][0] == "workspace:ws1"
