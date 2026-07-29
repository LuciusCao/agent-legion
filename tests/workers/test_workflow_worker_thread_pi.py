from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from server.app.jobs import JobQueries
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import (
    RecordingExecutor,
    RecordingPiExecutor,
    _local_node,
    _make_definition,
    _make_pi_worker,
    _make_worker,
)


def test_poll_updates_agent_status_for_pi_executor(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
    block_event = threading.Event()
    executor = RecordingPiExecutor("pi-default", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])
    agent_manager = MagicMock()

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_bindings
            (workspace_id, workflow_key, node_key, executor_id)
            values (%s, %s, %s, %s)
            """,
            (ws["id"], "test", "fetch", "pi-default"),
        )
        conn.execute(
            """
            insert into workspace_executor_allocations
            (workspace_id, executor_id, concurrency_limit)
            values (%s, %s, %s)
            """,
            (ws["id"], "pi-default", 2),
        )

    worker = _make_pi_worker(tmp_path, db_path, executor, [definition], agent_manager)
    worker._poll()

    # set_busy runs on the executor pool thread; wait for it before asserting.
    busy_deadline = time.monotonic() + 5
    while agent_manager.set_busy.call_count == 0 and time.monotonic() < busy_deadline:
        time.sleep(0.01)

    agent_manager.set_busy.assert_called_once()
    args, kwargs = agent_manager.set_busy.call_args
    assert args[0] == "pi"
    assert kwargs["workspace_id"] == ws["id"]
    assert args[1]["id"] == job["id"]
    assert args[1]["title"] == "Q1"
    assert args[1]["external_id"] == "Q1"
    assert args[1]["current_phase"] == "fetch"

    block_event.set()
    worker.stop()

    agent_manager.set_idle.assert_called_once_with("pi", workspace_id=ws["id"])


def test_poll_does_not_update_agent_status_for_local_executor(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
    executor = RecordingExecutor("code-default")
    definition = _make_definition([_local_node("fetch")])
    agent_manager = MagicMock()

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_bindings
            (workspace_id, workflow_key, node_key, executor_id)
            values (%s, %s, %s, %s)
            """,
            (ws["id"], "test", "fetch", "code-default"),
        )
        conn.execute(
            """
            insert into workspace_executor_allocations
            (workspace_id, executor_id, concurrency_limit)
            values (%s, %s, %s)
            """,
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker.agent_manager = agent_manager
    worker._poll()

    executor.block_event.set()
    worker.stop()

    agent_manager.set_busy.assert_not_called()
    agent_manager.set_idle.assert_not_called()
