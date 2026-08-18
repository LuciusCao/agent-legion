from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import (
    RecordingExecutor,
    _local_node,
    _make_definition,
    _make_worker,
    _seed_trivial_node_code,
)


def test_poll_persists_relative_log_path_and_keeps_context_absolute(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="demo_workflow")
    executor = RecordingExecutor("code-default")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    _seed_trivial_node_code(db_path, ws["id"], "test", "fetch")

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is True
    executor.block_event.set()
    worker.stop()

    runs = job_db.list_node_runs(job["id"])
    assert len(runs) == 1
    assert runs[0]["log_path"].startswith("logs/")
    assert not Path(runs[0]["log_path"]).is_absolute()

    assert len(executor.contexts) == 1
    assert executor.contexts[0].log_path.is_absolute()
