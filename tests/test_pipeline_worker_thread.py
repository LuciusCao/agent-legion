import threading
from unittest.mock import MagicMock

from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import (
    PipelineAgent,
    PipelineConcurrency,
    PipelineDefinition,
    PipelineNode,
)


def _make_def():
    return PipelineDefinition(
        key="test",
        label="Test",
        concurrency=PipelineConcurrency(local=2, agent=1, nodes={"n1": 2}),
        intake=MagicMock(),
        nodes={
            "n1": PipelineNode(key="n1", label="N1", runner="local", outputs=["o.json"]),
            "n2": PipelineNode(
                key="n2",
                label="N2",
                runner="agent",
                outputs=["o.json"],
                agent=PipelineAgent(engine="pi", skill="test_skill"),
            ),
        },
    )


def test_ensure_workspace_executors_creates_executors_and_sets_limits(tmp_path):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [_make_def()]
    worker._ensure_workspace_executors(ws["id"])

    assert ws["id"] in worker._ws_local_executors
    assert ws["id"] in worker._ws_agent_executors
    assert worker._ws_agent_limits[ws["id"]] == 1
    assert worker._ws_local_executors[ws["id"]]._max_workers >= 2
    assert worker._ws_agent_executors[ws["id"]]._max_workers == 1

    agents = job_db.list_workspace_agents(ws["id"])
    assert any(a["agent_id"] == "pi" and a["concurrency_limit"] == 1 for a in agents)

    worker.stop()


def test_poll_respects_per_workspace_agent_limits(tmp_path, monkeypatch):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [
        PipelineDefinition(
            key="test",
            label="Test",
            concurrency=PipelineConcurrency(local=2, agent=1, nodes={}),
            intake=MagicMock(),
            nodes={
                "n2": PipelineNode(
                    key="n2",
                    label="N2",
                    runner="agent",
                    outputs=["o.json"],
                    agent=PipelineAgent(engine="pi", skill="test_skill"),
                ),
            },
        )
    ]
    worker._pi_runner = MagicMock()
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    for i in range(2):
        job_db.create_job(
            pipeline_key="test",
            source_type="question",
            source_id=f"Q{i}",
            batch_id="",
            title=f"Question Q{i}",
            node_keys=["n2"],
            workspace_id=ws["id"],
        )

    blocker = threading.Event()

    def _slow_execute(
        job_db, definition, job, node_key, logs_dir, settings_config, pi_runner, skill_root
    ):
        if pi_runner is not None:
            blocker.wait(timeout=10)
        return True

    monkeypatch.setattr("server.app.pipeline_worker_thread._execute_node_wrapped", _slow_execute)

    worker._poll()

    assert len(worker._futures) == 1
    assert len(worker._ws_agent_futures.get(ws["id"], set())) == 1

    blocker.set()
    worker.stop()


def test_two_workspaces_with_limit_one_each_can_submit_simultaneously(tmp_path, monkeypatch):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws1 = job_db.create_workspace("Workspace One")
    ws2 = job_db.create_workspace("Workspace Two")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [
        PipelineDefinition(
            key="test",
            label="Test",
            concurrency=PipelineConcurrency(local=2, agent=1, nodes={}),
            intake=MagicMock(),
            nodes={
                "n2": PipelineNode(
                    key="n2",
                    label="N2",
                    runner="agent",
                    outputs=["o.json"],
                    agent=PipelineAgent(engine="pi", skill="test_skill"),
                ),
            },
        )
    ]
    worker._pi_runner = MagicMock()
    worker._skill_root = tmp_path / "skills"
    worker._skill_root.mkdir(parents=True)

    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Question Q1",
        node_keys=["n2"],
        workspace_id=ws1["id"],
    )
    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q2",
        batch_id="",
        title="Question Q2",
        node_keys=["n2"],
        workspace_id=ws2["id"],
    )

    blocker = threading.Event()

    def _slow_execute(
        job_db, definition, job, node_key, logs_dir, settings_config, pi_runner, skill_root
    ):
        if pi_runner is not None:
            blocker.wait(timeout=10)
        return True

    monkeypatch.setattr("server.app.pipeline_worker_thread._execute_node_wrapped", _slow_execute)

    worker._poll()

    assert len(worker._futures) == 2
    assert len(worker._ws_agent_futures.get(ws1["id"], set())) == 1
    assert len(worker._ws_agent_futures.get(ws2["id"], set())) == 1

    blocker.set()
    worker.stop()
