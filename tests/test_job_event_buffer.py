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


class FakeEventManager:
    def __init__(self):
        self.patch_batches = []
        self.resyncs = []

    def _broadcast(self, workspace_id: str, payload: str) -> None:
        data = json.loads(payload)
        if data["type"] == "job_patch_batch":
            self.patch_batches.append(
                (
                    workspace_id,
                    data["revision"],
                    data["stats"],
                    data["jobs"],
                    data["deleted_job_ids"],
                )
            )
        elif data["type"] == "resync_required":
            self.resyncs.append((workspace_id, data["latest_revision"], data["reason"]))


def test_aggregator_flushes_compacted_patch_batch():
    buffer = JobEventBuffer(max_events=10)
    buffer.record_job_updated("ws1", "job1")
    buffer.record_job_updated("ws1", "job1")
    buffer.record_job_deleted("ws1", "job2")
    queries = FakeJobQueries()
    manager = FakeEventManager()
    aggregator = WorkspaceJobEventAggregator(buffer, queries, manager)

    aggregator.flush_once()

    assert manager.patch_batches == [
        (
            "ws1",
            3,
            {"running": 1},
            [{"id": "job1", "workspace_id": "ws1"}],
            ["job2"],
        )
    ]
