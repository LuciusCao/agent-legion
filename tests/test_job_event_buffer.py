from server.app.job_events import JobEventBuffer


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
