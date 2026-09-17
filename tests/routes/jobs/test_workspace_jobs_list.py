"""GET /workspaces/{id}/jobs guard + run_id filter coverage.

#211 Phase 3 (workflow_key guard) and #735 (run_id filter: external callers
enumerate one run's jobs after POST /runs returned its job_ids — the legacy
endpoint's 500 cap makes the unfiltered list unreliable for that in a busy
workspace).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import publish_legacy_intake_revision
from tests.helpers.auth import authenticate_client

_WORKFLOW_KEY = "education_video_problems_generation"


def _create_runs_workspace(client) -> str:
    client.post(
        "/api/workspaces",
        json={"id": _WORKFLOW_KEY, "name": "jobs-run-filter-ws"},
    )
    publish_legacy_intake_revision(client.app.state.job_db, _WORKFLOW_KEY)
    return _WORKFLOW_KEY


def _insert_material(job_db, workspace_id: str, material_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
            ),
        )


def _create_run(client, workspace_id: str, material_ids: list[str]) -> dict:
    response = client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={
            "items": [{"type": "material", "material_id": mid} for mid in material_ids],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_list_jobs_rejects_mismatched_workflow_key(tmp_path, job_db):
    """Subagent review P3-1 on #307: guard parity with failed-node-runs —
    the deprecated query param can no longer narrow (read binding); a
    mismatched key is rejected instead of silently widening the list."""
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as client:
        job_db.create_workspace("ws-jobs-key", default_workflow_key="ws-jobs-key")

        mismatched = client.get("/api/workspaces/ws-jobs-key/jobs?workflow_key=other_flow")
        assert mismatched.status_code == 400, mismatched.text
        assert "workflow_key must equal the workspace id" in mismatched.json()["detail"]

        equal = client.get("/api/workspaces/ws-jobs-key/jobs?workflow_key=ws-jobs-key")
        assert equal.status_code == 200
        assert equal.json()["jobs"] == []


def test_list_jobs_filters_by_run_id(client, job_db):
    """#735：run_id 过滤只返回该 run 的 jobs——busy workspace 下外部系统
    靠它枚举本次提交的 job（配合 POST /runs 返回的 job_ids 交叉验证）。"""
    workspace_id = _create_runs_workspace(client)
    _insert_material(job_db, workspace_id, "mat-r1")
    _insert_material(job_db, workspace_id, "mat-r2")
    _insert_material(job_db, workspace_id, "mat-r3")
    first = _create_run(client, workspace_id, ["mat-r1", "mat-r2"])
    second = _create_run(client, workspace_id, ["mat-r3"])

    unfiltered = client.get(f"/api/workspaces/{workspace_id}/jobs")
    assert unfiltered.status_code == 200
    assert len(unfiltered.json()["jobs"]) == 3

    # 排序是 created_at desc；同一 run 的 job 在同一 bulk insert 里落库、
    # created_at 相同，顺序不稳定——断言集合恒等（过滤语义），顺序是
    # 列表端点自身的契约，不归 run_id 过滤管。
    for run_id, expected_ids in (
        (first["run"]["id"], first["job_ids"]),
        (second["run"]["id"], second["job_ids"]),
    ):
        response = client.get(f"/api/workspaces/{workspace_id}/jobs", params={"run_id": run_id})
        assert response.status_code == 200, response.text
        assert {job["id"] for job in response.json()["jobs"]} == set(expected_ids)
        assert len(response.json()["jobs"]) == len(expected_ids)


def test_list_jobs_run_id_filter_is_workspace_scoped(client, job_db):
    """#735 语义钉：run_id 是过滤参数不是资源寻址——跨 workspace 的 run_id
    返回空列表（不 404）。404 语义属于 run 详情端点（资源寻址面），把它
    塞进列表过滤会让「workspace 下无此 run」与「run 属于别的 workspace」
    不可区分，且与 status/source_id 等既有过滤参数的行为不一致。"""
    workspace_id = _create_runs_workspace(client)
    _insert_material(job_db, workspace_id, "mat-scope")
    run = _create_run(client, workspace_id, ["mat-scope"])
    run_id = run["run"]["id"]

    other_id = f"{workspace_id}-other"
    response = client.post(
        "/api/workspaces",
        json={"id": other_id, "name": "jobs-run-filter-ws-other"},
    )
    assert response.status_code == 200, response.text

    cross = client.get(f"/api/workspaces/{other_id}/jobs", params={"run_id": run_id})
    assert cross.status_code == 200, cross.text
    assert cross.json()["jobs"] == []

    missing = client.get(f"/api/workspaces/{workspace_id}/jobs", params={"run_id": "no-such-run"})
    assert missing.status_code == 200, missing.text
    assert missing.json()["jobs"] == []


def test_list_jobs_run_id_combines_with_status_filter(client, job_db):
    """#735：run_id 与既有 status 过滤可叠加（同一 WHERE 子句的合取）。"""
    workspace_id = _create_runs_workspace(client)
    _insert_material(job_db, workspace_id, "mat-comb")
    run = _create_run(client, workspace_id, ["mat-comb"])

    queued = client.get(
        f"/api/workspaces/{workspace_id}/jobs",
        params={"run_id": run["run"]["id"], "status": "queued"},
    )
    assert queued.status_code == 200
    assert {job["id"] for job in queued.json()["jobs"]} == set(run["job_ids"])

    running = client.get(
        f"/api/workspaces/{workspace_id}/jobs",
        params={"run_id": run["run"]["id"], "status": "running"},
    )
    assert running.status_code == 200
    assert running.json()["jobs"] == []
