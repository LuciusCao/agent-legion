from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app.cms.question import CmsQuestionDetail
from server.app.jobs import JobQueries
from server.app.workflows.definition import (
    WorkflowNode,
    load_workflow_definition,
)
from server.app.workflows.executor import (
    _execute_node_wrapped,
    execute_node_once,
    process_ready_workflow_node,
)
from tests.workers.helpers import _make_test_definition


def test_execute_fetch_question_context_writes_artifact(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
        jobs_dir=tmp_path / "jobs",
    )

    assert completed is True
    artifact = tmp_path / job["storage_dir"] / "question_context.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == {
        "question_id": "Q100",
        "title": "Question Q100",
        "source_type": "question_id",
        "normalized": {},
        "cms_payload": None,
    }
    assert queries.get_job_node(job["id"], "fetch_question_context")["status"] == "completed"


def test_execute_fetch_question_context_uses_cms_question_detail(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))
    workspace = queries.create_workspace(
        "Math V5",
        cms_config={"question_detail_url": "https://cms.example/question/detail?subject_id=5"},
    )
    batch = queries.create_batch(
        "question_content",
        "question_ids",
        {
            "question_ids": ["Q100"],
            "cms_config": {
                "question_detail_url": "https://cms.example/question/detail?subject_id=9"
            },
        },
        workspace_id=workspace["id"],
    )
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q100",
        batch_id=batch["id"],
        title="Question Q100",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    calls = []

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        calls.append({"question_id": question_id, "api_url": api_url, "token": token})
        return CmsQuestionDetail(
            question_id="Q100",
            title="CMS 题目一",
            normalized={"stem": "1 + 1 = ?"},
            payload={"data": {"uuid": "Q100", "title": "CMS 题目一"}},
        )

    monkeypatch.setattr(
        "server.app.workflows.question_content.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.workflows.question_content.get_token", lambda env, config: "token"
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
        settings_config={"cms": {"env": "prod"}},
        jobs_dir=tmp_path / "jobs",
    )

    assert completed is True
    assert calls == [
        {
            "question_id": "Q100",
            "api_url": "https://cms.example/question/detail?subject_id=9",
            "token": "token",
        }
    ]
    artifact = tmp_path / job["storage_dir"] / "question_context.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == {
        "question_id": "Q100",
        "title": "CMS 题目一",
        "source_type": "question_id",
        "normalized": {"stem": "1 + 1 = ?"},
        "cms_payload": {"data": {"uuid": "Q100", "title": "CMS 题目一"}},
    }


def test_fetch_question_context_uses_question_detail_resource_binding(tmp_path, monkeypatch):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))
    workspace = queries.create_workspace(
        "Resource Math",
        resource_config={
            "resources": {
                "question_detail": {
                    "provider": "cms.question.detail",
                    "config": {
                        "bank_version": "v5",
                        "subject_id": "5",
                    },
                }
            }
        },
    )
    batch = queries.create_batch(
        "question_content",
        "question_ids",
        {"question_ids": ["Q200"]},
        workspace_id=workspace["id"],
    )
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q200",
        batch_id=batch["id"],
        title="Question Q200",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    calls = []

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        calls.append({"question_id": question_id, "api_url": api_url, "token": token})
        return CmsQuestionDetail(
            question_id="Q200",
            title="资源绑定题目",
            normalized={},
            payload={"data": {"uuid": "Q200"}},
        )

    monkeypatch.setattr(
        "server.app.workflows.question_content.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.workflows.question_content.get_token", lambda env, config: "token"
    )

    completed = execute_node_once(
        job_db=queries,
        definition=definition,
        job=job,
        node_key="fetch_question_context",
        logs_dir=tmp_path / "logs",
        settings_config={
            "cms": {"env": "prod"},
            "resource_providers": {
                "cms.question.detail": {
                    "api_url": "https://cms.example/question/detail",
                }
            },
        },
        jobs_dir=tmp_path / "jobs",
    )

    assert completed is True
    assert calls == [
        {
            "question_id": "Q200",
            "api_url": "https://cms.example/question/detail?bank_version=v5&subject_id=5",
            "token": "token",
        }
    ]


def test_process_ready_workflow_node_runs_root(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q101",
        batch_id="",
        title="Question Q101",
        node_keys=list(definition.nodes),
    )

    processed = process_ready_workflow_node(
        job_db=queries,
        definition=definition,
        logs_dir=tmp_path / "logs",
        jobs_dir=tmp_path / "jobs",
    )

    assert processed is True
    assert (tmp_path / job["storage_dir"] / "question_context.json").exists()


def test_execute_local_node_once_fails_when_handler_missing(tmp_path):
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    definition = load_workflow_definition(Path("config/workflows/question_content.yaml"))
    job = queries.create_job(
        workflow_key="question_content",
        source_type="question_id",
        source_id="Q102",
        batch_id="",
        title="Question Q102",
        node_keys=list(definition.nodes),
    )

    from server.app.workflows.executor import execute_local_node_once

    with pytest.raises(ValueError, match="No local handler"):
        execute_local_node_once(
            job_db=queries,
            definition=definition,
            job=job,
            node_key="assemble_package",
            logs_dir=tmp_path / "logs",
            jobs_dir=tmp_path / "jobs",
        )


def test_workflow_worker_does_not_start_when_app_worker_disabled(tmp_path, monkeypatch):
    from server.app import main as app_main

    started: list[bool] = []

    class FakeWorkflowWorkerThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            started.append(True)

        def stop(self, timeout: float = 3):
            pass

    monkeypatch.setattr(app_main, "WorkflowWorkerThread", FakeWorkflowWorkerThread)
    app = app_main.create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True

    with TestClient(app):
        pass

    assert started == []


def test_execute_node_wrapped_falls_back_to_direct_update_when_no_run_exists():
    job_db = MagicMock()
    job_db.list_node_runs.return_value = []
    definition = MagicMock()
    job = {"id": "job_1"}

    with patch(
        "server.app.workflows.executor.execute_node_once",
        side_effect=RuntimeError("boom"),
    ):
        result = _execute_node_wrapped(
            job_db,
            definition,
            job,
            "node_a",
            Path("/tmp/logs"),
            None,
            None,
            None,
        )

    assert result is False
    job_db.finish_node_run.assert_not_called()
    job_db.update_job_node.assert_called_once_with(
        "job_1", "node_a", status="failed", error_message="boom"
    )
    job_db.update_job_status.assert_called_once_with("job_1", "failed", "boom")


def test_execute_node_wrapped_finishes_latest_running_run_when_one_exists():
    job_db = MagicMock()
    job_db.list_node_runs.return_value = [
        {"id": 1, "node_key": "node_a", "status": "completed"},
        {"id": 2, "node_key": "node_a", "status": "running"},
        {"id": 3, "node_key": "node_b", "status": "running"},
    ]
    definition = MagicMock()
    job = {"id": "job_1"}

    with patch(
        "server.app.workflows.executor.execute_node_once",
        side_effect=RuntimeError("boom"),
    ):
        result = _execute_node_wrapped(
            job_db,
            definition,
            job,
            "node_a",
            Path("/tmp/logs"),
            None,
            None,
            None,
        )

    assert result is False
    job_db.finish_node_run.assert_called_once_with(2, "failed", 1, "boom")
    job_db.update_job_node.assert_not_called()
    job_db.update_job_status.assert_called_once_with("job_1", "failed", "boom")


def test_process_ready_workflow_node_refreshes_job_status_when_no_local_nodes_ready():
    definition = _make_test_definition(
        [
            WorkflowNode(
                key="agent_node",
                label="Agent",
                capability="agent_node",
            ),
        ]
    )
    job_db = MagicMock()
    job = {"id": "job_1", "storage_dir": "/tmp/job_1"}
    job_db.list_jobs.return_value = [job]

    ready_node = MagicMock()

    with (
        patch(
            "server.app.workflows.executor.find_ready_nodes",
            return_value=[ready_node],
        ) as mock_find,
        patch("server.app.workflows.executor._refresh_job_status") as mock_refresh,
        patch(
            "server.app.workflows.executor._node_statuses",
            return_value={"agent_node": "pending"},
        ),
    ):
        result = process_ready_workflow_node(
            job_db, definition, Path("/tmp/logs"), jobs_dir=Path("/tmp")
        )

    assert result is False
    mock_find.assert_called_once()
    mock_refresh.assert_called_once_with(job_db, "job_1")
