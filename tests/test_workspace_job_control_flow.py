"""End-to-end control-flow acceptance for Workspace DAG jobs.

This test proves that the generic Workspace UI can list, run-to, continue, rerun,
package, and delete jobs using only the API and persisted Node state.  It uses a
branched test workflow and fake Executors so it does not require real ASR,
openclaw, or Pi binaries.
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.runtime import ExecutionRuntime
from server.app.main import create_app
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.storage_paths import resolve_job_dir
from server.app.workflow_worker.execution import reap_futures
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import load_workflow_definition
from tests.helpers.auth import authenticate_client

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_KEY = "test_control_flow"


def _write_test_workflow(tmp_path: Path) -> Path:
    path = tmp_path / f"{WORKFLOW_KEY}.yaml"
    path.write_text(
        "key: test_control_flow\n"
        "label: Control Flow Test\n"
        "intake:\n"
        "  modes:\n"
        "    direct_ids:\n"
        "      label: Direct IDs\n"
        "      input_field: question_ids\n"
        "nodes:\n"
        "  prepare:\n"
        "    label: Prepare\n"
        "    capability: prepare\n"
        "    outputs: [prepare.json]\n"
        "  branch_a:\n"
        "    label: Branch A\n"
        "    capability: branch_a\n"
        "    after: [prepare]\n"
        "    inputs: [prepare.json]\n"
        "    outputs: [branch_a.json]\n"
        "  branch_b:\n"
        "    label: Branch B\n"
        "    capability: branch_b\n"
        "    after: [prepare]\n"
        "    inputs: [prepare.json]\n"
        "    outputs: [branch_b.json]\n"
        "  merge:\n"
        "    label: Merge\n"
        "    capability: merge\n"
        "    after: [branch_a, branch_b]\n"
        "    inputs: [branch_a.json, branch_b.json]\n"
        "    outputs: [merge.json]\n",
        encoding="utf-8",
    )
    return path


def _patch_workflow(monkeypatch, workflow_path: Path) -> None:
    """Inject the synthetic test workflow into the catalog service.

    The DB-backed catalog gates workspace binding through ``bound_definition``
    while runtime fallbacks call ``definition``; both resolve WORKFLOW_KEY to
    the synthetic file-backed definition here.
    """
    _original_definition = WorkflowCatalogService.definition
    _original_bound = WorkflowCatalogService.bound_definition

    def _patched_definition(self, workflow_key: str):
        if workflow_key == WORKFLOW_KEY:
            return load_workflow_definition(workflow_path)
        return _original_definition(self, workflow_key)

    def _patched_bound(self, workflow_key: str):
        if workflow_key == WORKFLOW_KEY:
            return load_workflow_definition(workflow_path)
        return _original_bound(self, workflow_key)

    monkeypatch.setattr(
        "server.app.services.workflow_catalog.WorkflowCatalogService.definition",
        _patched_definition,
    )
    monkeypatch.setattr(
        "server.app.services.workflow_catalog.WorkflowCatalogService.bound_definition",
        _patched_bound,
    )


class _RecordingExecutor:
    kind = "code"
    id = "code"

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.runs.append(
            {
                "job_id": context.job_id,
                "node_key": context.node_key,
                "capability": context.capability,
            }
        )
        context.job_dir.mkdir(parents=True, exist_ok=True)
        context.log_path.parent.mkdir(parents=True, exist_ok=True)
        for name in context.expected_outputs:
            (context.job_dir / name).write_text(
                f'{{"node":"{context.node_key}","capability":"{context.capability}"}}',
                encoding="utf-8",
            )
        context.log_path.write_text(f"completed {context.node_key}\n", encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            log_path=str(context.log_path),
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        pass


def _make_worker(
    job_db: Any,
    leases: ExecutorLeaseRepository,
    executor: Any,
    settings: Any,
    definition: Any,
) -> WorkflowWorkerThread:
    runtime = ExecutionRuntime(
        leases=leases,
        executor=executor,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=30,
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        runtime=runtime,
        settings=settings,
    )
    worker._scan_entries = ([definition], [])
    worker._ensure_pools()
    return worker


def _drain(worker: WorkflowWorkerThread, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        worker._poll()
        reap_futures(worker)
        if not worker._futures and not worker._poll():
            # One more pass to claim any newly-ready nodes.
            break
        time.sleep(0.05)


def _configure_workspace(job_db: Any, workspace_id: str, workflow_key: str) -> None:
    node_keys = ["prepare", "branch_a", "branch_b", "merge"]
    with job_db.connect() as conn:
        for node_key in node_keys:
            conn.execute(
                """
                insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit)
                values (%s, %s, %s, %s)
                on conflict(workspace_id, workflow_key, node_key) do update set concurrency_limit=excluded.concurrency_limit
                """,
                (workspace_id, workflow_key, node_key, 4),
            )


def _node_statuses(detail: dict[str, Any]) -> dict[str, str]:
    return {node["node_key"]: node["status"] for node in detail["nodes"]}


def test_workspace_job_control_flow(tmp_path, monkeypatch):
    workflow_path = _write_test_workflow(tmp_path)
    _patch_workflow(monkeypatch, workflow_path)

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    with authenticate_client(TestClient(app)) as client:
        ws_response = client.post(
            "/api/workspaces",
            json={"name": "Control Flow", "default_workflow_key": WORKFLOW_KEY},
        )
        assert ws_response.status_code == 200
        workspace_id = ws_response.json()["workspace"]["id"]

        _configure_workspace(app.state.job_db, workspace_id, WORKFLOW_KEY)

        # Post-#96 every code node needs published code to dispatch (P-0.5:
        # no executor-capability fallback); the recording executor never
        # reads the text.
        from server.app.services.node_codes import NodeCodeService

        codes = NodeCodeService(app.state.job_db.path)
        for node_key in ("prepare", "branch_a", "branch_b", "merge"):
            codes.save_draft(
                workspace_id,
                WORKFLOW_KEY,
                node_key,
                "def run(job, job_dir, runtime):\n    pass\n",
                "test seed",
            )
            codes.publish(workspace_id, WORKFLOW_KEY, node_key)

        batch_response = client.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": WORKFLOW_KEY,
                "source_kind": "direct_ids",
                "question_ids": ["C1"],
                "knowledge_codes": [],
            },
        )
        assert batch_response.status_code == 200
        job_id = batch_response.json()["jobs"][0]["id"]

        # 1. List summary shows persisted Nodes.
        list_response = client.get(f"/api/workspaces/{workspace_id}/jobs")
        assert list_response.status_code == 200
        jobs = list_response.json()["jobs"]
        assert any(job["id"] == job_id for job in jobs)
        job_summary = next(job for job in jobs if job["id"] == job_id)
        assert job_summary["total_nodes"] == 4
        assert job_summary["completed_nodes"] == 0

        definition = WorkflowCatalogService(app.state.settings).definition(WORKFLOW_KEY)
        leases = ExecutorLeaseRepository(
            app.state.job_db.path, data_dir=app.state.settings.data_dir
        )
        worker = _make_worker(
            app.state.job_db, leases, _RecordingExecutor(), app.state.settings, definition
        )

        # 2. run-to claims only the target closure.
        run_to_response = client.post(
            f"/api/jobs/{job_id}/run-to",
            json={"target_node_key": "branch_a"},
        )
        assert run_to_response.status_code == 200
        assert run_to_response.json()["status"] == "succeeded"

        _drain(worker)

        # 3. Target completion pauses before descendants.
        detail_response = client.get(f"/api/jobs/{job_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        statuses = _node_statuses(detail)
        assert statuses["prepare"] == "completed"
        assert statuses["branch_a"] == "completed"
        assert statuses["branch_b"] == "pending"
        assert statuses["merge"] == "pending"
        assert detail["job"]["status"] == "paused"
        assert detail["job"]["execution_control"]["paused"] is True

        # 4. continue completes the DAG.
        continue_response = client.post(f"/api/jobs/{job_id}/continue", json={})
        assert continue_response.status_code == 200
        assert continue_response.json()["status"] == "succeeded"

        _drain(worker)

        detail_response = client.get(f"/api/jobs/{job_id}")
        detail = detail_response.json()
        statuses = _node_statuses(detail)
        assert statuses["prepare"] == "completed"
        assert statuses["branch_a"] == "completed"
        assert statuses["branch_b"] == "completed"
        assert statuses["merge"] == "completed"
        assert detail["job"]["status"] == "completed"

        # 5. rerun invalidates only selected Node and descendants.
        rerun_response = client.post(f"/api/jobs/{job_id}/nodes/branch_a/rerun")
        assert rerun_response.status_code == 200
        assert rerun_response.json()["status"] == "succeeded"

        _drain(worker)

        detail_response = client.get(f"/api/jobs/{job_id}")
        detail = detail_response.json()
        statuses = _node_statuses(detail)
        assert statuses["prepare"] == "completed"
        assert statuses["branch_a"] == "completed"
        assert statuses["branch_b"] == "completed"
        assert statuses["merge"] == "completed"

        # 6. logs are readable through the safe endpoint.
        runs = detail["runs"]
        assert runs
        run_id = runs[-1]["id"]
        log_response = client.get(f"/api/jobs/{job_id}/runs/{run_id}/log")
        assert log_response.status_code == 200
        assert "completed" in log_response.json()["log"]

        # 7. package includes completed Job artifacts.
        package_response = client.post(
            f"/api/workspaces/{workspace_id}/jobs/package",
            json={"job_ids": [job_id]},
        )
        assert package_response.status_code == 200
        package_body = package_response.json()
        assert package_body["succeeded_count"] == 1
        assert package_body["download_url"]
        filename = package_body["package_filename"]

        download_response = client.get(f"/api/workspaces/{workspace_id}/packages/{filename}")
        assert download_response.status_code == 200

        with zipfile.ZipFile(io.BytesIO(download_response.content)) as zf:
            names = zf.namelist()
        assert "manifest.json" in names
        assert not any("prepare.json" in name for name in names)
        assert not any("merge.json" in name for name in names)

        # 8. delete removes database rows, storage, and logs.
        job = app.state.job_db.get_job(job_id)
        storage_dir = resolve_job_dir(job, app.state.settings.jobs_dir)
        log_dir = app.state.settings.logs_dir / "jobs"
        log_files = list(log_dir.glob(f"{job_id}-*.log"))
        assert storage_dir.exists()
        assert log_files

        delete_response = client.delete(f"/api/jobs/{job_id}")
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] == job_id

        assert not storage_dir.exists()
        assert not list(log_dir.glob(f"{job_id}-*.log"))
        assert app.state.job_db.get_job(job_id) is None
        assert app.state.job_db.list_job_nodes(job_id) == []
        assert app.state.job_db.list_node_runs(job_id) == []

        worker.stop()


def test_continue_job_rejects_terminal_states(tmp_path, monkeypatch):
    workflow_path = _write_test_workflow(tmp_path)
    _patch_workflow(monkeypatch, workflow_path)

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    with authenticate_client(TestClient(app)) as client:
        ws_response = client.post(
            "/api/workspaces",
            json={"name": "Terminal State", "default_workflow_key": WORKFLOW_KEY},
        )
        assert ws_response.status_code == 200
        workspace_id = ws_response.json()["workspace"]["id"]
        _configure_workspace(app.state.job_db, workspace_id, WORKFLOW_KEY)

        # Post-#96 every code node needs published code to dispatch (P-0.5:
        # no executor-capability fallback); the recording executor never
        # reads the text.
        from server.app.services.node_codes import NodeCodeService

        codes = NodeCodeService(app.state.job_db.path)
        for node_key in ("prepare", "branch_a", "branch_b", "merge"):
            codes.save_draft(
                workspace_id,
                WORKFLOW_KEY,
                node_key,
                "def run(job, job_dir, runtime):\n    pass\n",
                "test seed",
            )
            codes.publish(workspace_id, WORKFLOW_KEY, node_key)

        batch_response = client.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": WORKFLOW_KEY,
                "source_kind": "direct_ids",
                "question_ids": ["T1"],
                "knowledge_codes": [],
            },
        )
        assert batch_response.status_code == 200
        job_id = batch_response.json()["jobs"][0]["id"]

        for terminal_status in ("completed", "failed", "cancelled"):
            app.state.job_db.update_job_status(job_id, terminal_status)
            app.state.job_db.pause_job(job_id, "test")
            detail_before = client.get(f"/api/jobs/{job_id}").json()

            response = client.post(f"/api/jobs/{job_id}/continue", json={})

            assert response.status_code == 400, terminal_status
            assert response.json()["detail"]
            detail_after = client.get(f"/api/jobs/{job_id}").json()
            assert detail_after["job"]["status"] == terminal_status
            assert detail_after["job"]["execution_control"]["paused"] is True
            assert detail_after["nodes"] == detail_before["nodes"]


def test_continue_job_resumes_paused_state(tmp_path, monkeypatch):
    workflow_path = _write_test_workflow(tmp_path)
    _patch_workflow(monkeypatch, workflow_path)

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    with authenticate_client(TestClient(app)) as client:
        ws_response = client.post(
            "/api/workspaces",
            json={"name": "Paused State", "default_workflow_key": WORKFLOW_KEY},
        )
        assert ws_response.status_code == 200
        workspace_id = ws_response.json()["workspace"]["id"]
        _configure_workspace(app.state.job_db, workspace_id, WORKFLOW_KEY)

        # Post-#96 every code node needs published code to dispatch (P-0.5:
        # no executor-capability fallback); the recording executor never
        # reads the text.
        from server.app.services.node_codes import NodeCodeService

        codes = NodeCodeService(app.state.job_db.path)
        for node_key in ("prepare", "branch_a", "branch_b", "merge"):
            codes.save_draft(
                workspace_id,
                WORKFLOW_KEY,
                node_key,
                "def run(job, job_dir, runtime):\n    pass\n",
                "test seed",
            )
            codes.publish(workspace_id, WORKFLOW_KEY, node_key)

        batch_response = client.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": WORKFLOW_KEY,
                "source_kind": "direct_ids",
                "question_ids": ["P1"],
                "knowledge_codes": [],
            },
        )
        assert batch_response.status_code == 200
        job_id = batch_response.json()["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "paused")
        app.state.job_db.pause_job(job_id, "target_reached")

        response = client.post(f"/api/jobs/{job_id}/continue", json={})
        detail = client.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert detail["job"]["status"] == "queued"
    assert detail["job"]["execution_control"]["paused"] is False
    assert detail["job"]["execution_control"]["mode"] == "full"
