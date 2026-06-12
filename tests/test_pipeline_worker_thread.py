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
            "n1": PipelineNode(
                key="n1", label="N1", capability="n1", runner="local", outputs=["o.json"]
            ),
            "n2": PipelineNode(
                key="n2",
                label="N2",
                capability="n2",
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
                    capability="n2",
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


def test_ensure_workspace_executors_uses_pipeline_config_overrides(tmp_path):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", pipeline_config={"local": 5, "nodes": {"n1": 3}})

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [_make_def()]
    worker._ensure_workspace_executors(ws["id"])

    # local_default=5, node_limit_sum=3, local_limit=max(5, 3)=5
    assert worker._ws_local_executors[ws["id"]]._max_workers == 5
    assert worker._ws_agent_executors[ws["id"]]._max_workers == 1
    worker.stop()


def test_ensure_workspace_executors_recreates_on_local_limit_change(tmp_path):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [_make_def()]
    worker._ensure_workspace_executors(ws["id"])

    old_local = worker._ws_local_executors[ws["id"]]
    old_agent = worker._ws_agent_executors[ws["id"]]

    # Update pipeline_config to change local limit
    job_db.update_workspace(ws["id"], pipeline_config={"local": 10})
    worker._ensure_workspace_executors(ws["id"])

    # local_default=10, node_limit_sum=2, local_limit=max(10, 2)=10
    assert worker._ws_local_executors[ws["id"]]._max_workers == 10
    assert worker._ws_local_executors[ws["id"]] is not old_local
    assert worker._ws_agent_executors[ws["id"]] is not old_agent
    worker.stop()


def test_ensure_workspace_executors_skips_recreation_when_limits_unchanged(tmp_path):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    worker = PipelineWorkerThread(job_db, settings)
    worker._definitions = [_make_def()]
    worker._ensure_workspace_executors(ws["id"])

    old_local = worker._ws_local_executors[ws["id"]]
    old_agent = worker._ws_agent_executors[ws["id"]]

    # Call again without changing config
    worker._ensure_workspace_executors(ws["id"])

    assert worker._ws_local_executors[ws["id"]] is old_local
    assert worker._ws_agent_executors[ws["id"]] is old_agent
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
                    capability="n2",
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


def test_poll_respects_per_node_local_limit(tmp_path, monkeypatch):
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
            concurrency=PipelineConcurrency(local=4, agent=1, nodes={"n1": 1}),
            intake=MagicMock(),
            nodes={
                "n1": PipelineNode(
                    key="n1", label="N1", capability="n1", runner="local", outputs=["o.json"]
                ),
            },
        )
    ]

    for i in range(3):
        job_db.create_job(
            pipeline_key="test",
            source_type="question",
            source_id=f"Q{i}",
            batch_id="",
            title=f"Question Q{i}",
            node_keys=["n1"],
            workspace_id=ws["id"],
        )

    blocker = threading.Event()

    def _slow_execute(
        job_db, definition, job, node_key, logs_dir, settings_config, pi_runner, skill_root
    ):
        blocker.wait(timeout=10)
        return True

    monkeypatch.setattr("server.app.pipeline_worker_thread._execute_node_wrapped", _slow_execute)

    worker._poll()

    # per-node limit is 1, so only 1 job should be submitted despite local=4
    assert len(worker._futures) == 1
    assert len(worker._ws_local_futures.get((ws["id"], "n1"), set())) == 1

    blocker.set()
    worker.stop()


def test_poll_respects_total_local_executor_limit(tmp_path, monkeypatch):
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
            concurrency=PipelineConcurrency(local=1, agent=1, nodes={}),
            intake=MagicMock(),
            nodes={
                "n1": PipelineNode(
                    key="n1", label="N1", capability="n1", runner="local", outputs=["o.json"]
                ),
                "n2": PipelineNode(
                    key="n2", label="N2", capability="n2", runner="local", outputs=["o2.json"]
                ),
            },
        )
    ]

    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Question Q1",
        node_keys=["n1", "n2"],
        workspace_id=ws["id"],
    )
    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q2",
        batch_id="",
        title="Question Q2",
        node_keys=["n1", "n2"],
        workspace_id=ws["id"],
    )

    blocker = threading.Event()

    def _slow_execute(
        job_db, definition, job, node_key, logs_dir, settings_config, pi_runner, skill_root
    ):
        blocker.wait(timeout=10)
        return True

    monkeypatch.setattr("server.app.pipeline_worker_thread._execute_node_wrapped", _slow_execute)

    worker._poll()

    # local executor max_workers=2 (max of local_default=1 and node_limit_sum=2)
    # but per-node limit is 1, so each node only gets 1 future
    assert len(worker._futures) == 2
    assert len(worker._ws_local_futures.get((ws["id"], "n1"), set())) == 1
    assert len(worker._ws_local_futures.get((ws["id"], "n2"), set())) == 1

    blocker.set()
    worker.stop()


def test_poll_skips_paused_workspace(tmp_path, monkeypatch):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    paused_ws = {ws["id"]: True}
    worker_control = MagicMock()
    worker_control.is_paused = lambda ws_id: paused_ws.get(ws_id, False)

    worker = PipelineWorkerThread(job_db, settings, workspace_worker_control=worker_control)
    worker._definitions = [
        PipelineDefinition(
            key="test",
            label="Test",
            concurrency=PipelineConcurrency(local=2, agent=1, nodes={}),
            intake=MagicMock(),
            nodes={
                "n1": PipelineNode(
                    key="n1", label="N1", capability="n1", runner="local", outputs=["o.json"]
                ),
            },
        )
    ]

    job_db.create_job(
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Question Q1",
        node_keys=["n1"],
        workspace_id=ws["id"],
    )

    executed = []

    def _fast_execute(
        job_db, definition, job, node_key, logs_dir, settings_config, pi_runner, skill_root
    ):
        executed.append(job["source_id"])
        return True

    monkeypatch.setattr("server.app.pipeline_worker_thread._execute_node_wrapped", _fast_execute)

    worker._poll()

    # workspace is paused, no jobs should be submitted
    assert len(worker._futures) == 0
    assert len(executed) == 0

    # unpause and poll again
    paused_ws[ws["id"]] = False
    worker._poll()

    assert len(worker._futures) == 1
    assert len(executed) == 1

    worker.stop()


def test_ensure_workspace_executors_calls_agent_manager(tmp_path):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    agent_manager = MagicMock()
    worker = PipelineWorkerThread(job_db, settings, agent_manager=agent_manager)
    worker._definitions = [_make_def()]
    worker._ensure_workspace_executors(ws["id"])

    agent_manager.add_pi_agent_for_workspace.assert_called_once_with(ws["id"], 1)
    worker.stop()


def test_poll_calls_agent_manager_busy_and_idle(tmp_path, monkeypatch):
    job_db = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")

    settings = MagicMock()
    settings.logs_dir = tmp_path / "logs"
    settings.config = {}

    agent_manager = MagicMock()
    worker = PipelineWorkerThread(job_db, settings, agent_manager=agent_manager)
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
                    capability="n2",
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
    agent_manager.set_busy.assert_called_once_with(
        "pi", job_db.get_job(job_id="test_ws_test_Q1"), workspace_id=ws["id"]
    )

    blocker.set()
    # Allow future to complete and be reaped
    import time

    time.sleep(0.1)
    worker._poll()

    agent_manager.set_idle.assert_called_once_with("pi", workspace_id=ws["id"])
    worker.stop()
