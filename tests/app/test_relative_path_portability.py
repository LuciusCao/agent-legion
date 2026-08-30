import shutil
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_logs import JobLogService
from server.app.services.job_queries import JobQueryService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import load_settings
from server.app.storage_paths import resolve_data_path, resolve_job_dir
from tests.helpers import publish_builtin_revision
from tests.postgres_support import TEST_DATABASE_URL

WORKSPACE_ID = "default"
WORKFLOW_KEY = "education_video_problems_generation"
SOURCE_ID = "S123"
JOB_ID = f"{WORKSPACE_ID}_{WORKFLOW_KEY}_{SOURCE_ID}"
NODE_KEY = "fetch_items"
RUN_TOKEN = "run-abc"


def _seed_old_root(old_root: Path) -> None:
    """Create files and a database under *old_root* with canonical relative paths."""
    db_path = TEST_DATABASE_URL
    init_db(db_path)

    # Managed directories (mirroring the canonical relative values stored below).
    job_dir = old_root / "jobs" / WORKSPACE_ID / JOB_ID
    job_log_dir = old_root / "logs" / "jobs"

    job_dir.mkdir(parents=True)
    job_log_dir.mkdir(parents=True)

    # Job artifact used by JobArtifactService / job artifact route.
    (job_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")

    # Run/session directories used by JobQueryService path projection.
    session_dir = job_dir / "runs" / "node" / RUN_TOKEN / "session"
    session_dir.mkdir(parents=True)
    (session_dir / ".keep").write_text("", encoding="utf-8")

    # Logs used by JobLogService.
    (job_log_dir / f"{JOB_ID}-{NODE_KEY}.log").write_text(
        "node run complete from old_root\n", encoding="utf-8"
    )

    with write_transaction(db_path) as conn:
        conn.execute(
            """
            insert into workspaces(id, name, default_workflow_key, default_entity)
            values ('default', 'Default', 'education_video_problems_generation', 'question')
            """
        )
        conn.execute(
            """
            insert into jobs(
                id, workspace_id, workflow_key, source_type, source_id,
                run_id, title, storage_dir, status
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                JOB_ID,
                WORKSPACE_ID,
                WORKFLOW_KEY,
                "question",
                SOURCE_ID,
                "batch1",
                "Test Job",
                f"jobs/{WORKSPACE_ID}/{JOB_ID}",
                "completed",
            ),
        )
        conn.execute(
            """
            insert into node_runs(
                job_id, node_key, status, log_path, run_dir, session_dir
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                JOB_ID,
                NODE_KEY,
                "completed",
                f"logs/jobs/{JOB_ID}-{NODE_KEY}.log",
                f"jobs/{WORKSPACE_ID}/{JOB_ID}/runs/node/{RUN_TOKEN}",
                f"jobs/{WORKSPACE_ID}/{JOB_ID}/runs/node/{RUN_TOKEN}/session",
            ),
        )


@pytest.fixture
def portable_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Return *(old_root, new_root)* with a copied, portable Agent Legion tree."""
    old_root = tmp_path / "old_root"
    new_root = tmp_path / "new_root"
    old_root.mkdir()
    _seed_old_root(old_root)

    # Copy the managed file tree to the new root; PostgreSQL remains shared.
    shutil.copytree(old_root, new_root)

    return old_root, new_root


def test_cross_root_path_portability(portable_roots: tuple[Path, Path]) -> None:
    """Canonical relative paths stay unchanged while services resolve to *new_root*."""
    old_root, new_root = portable_roots

    settings = load_settings(data_dir=new_root)
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=settings.jobs_dir)

    # ------------------------------------------------------------------
    # 1. Database values are still the original canonical relative paths.
    # ------------------------------------------------------------------
    job = job_db.get_job(JOB_ID)
    assert job is not None
    assert job["storage_dir"] == f"jobs/{WORKSPACE_ID}/{JOB_ID}"

    node_runs = job_db.list_node_runs(JOB_ID)
    assert len(node_runs) == 1
    run = node_runs[0]
    assert run["log_path"] == f"logs/jobs/{JOB_ID}-{NODE_KEY}.log"
    assert run["run_dir"] == f"jobs/{WORKSPACE_ID}/{JOB_ID}/runs/node/{RUN_TOKEN}"
    assert run["session_dir"] == f"jobs/{WORKSPACE_ID}/{JOB_ID}/runs/node/{RUN_TOKEN}/session"

    # ------------------------------------------------------------------
    # 2. Low-level resolvers point under *new_root*.
    # ------------------------------------------------------------------
    resolved_job_dir = resolve_job_dir(job, settings.jobs_dir)
    assert resolved_job_dir == (settings.jobs_dir / WORKSPACE_ID / JOB_ID).resolve()
    assert resolved_job_dir.is_relative_to(new_root.resolve())

    resolved_node_log = resolve_data_path(run["log_path"], settings.data_dir, allow_missing=True)
    assert resolved_node_log == (settings.logs_dir / "jobs" / f"{JOB_ID}-{NODE_KEY}.log").resolve()

    resolved_run_dir = resolve_data_path(run["run_dir"], settings.data_dir, allow_missing=True)
    assert (
        resolved_run_dir
        == (settings.jobs_dir / WORKSPACE_ID / JOB_ID / "runs" / "node" / RUN_TOKEN).resolve()
    )

    # ------------------------------------------------------------------
    # 3. Service reads resolve to *new_root*.
    # ------------------------------------------------------------------
    artifact_service = JobArtifactService(job_db)
    artifact = artifact_service.read(JOB_ID, "result.json")
    assert artifact["content"] == '{"ok": true}'

    log_service = JobLogService(settings, job_db)
    log_result = log_service.read(JOB_ID, int(run["id"]))
    assert "old_root" in log_result["log"]

    workspace_execution_config = WorkspaceExecutionConfigurationService(job_db)
    job_queries = JobQueryService(job_db, settings, workspace_execution_config)
    # Snapshot-less job: its definition resolves from the workspace's active
    # revision (schema v50), so publish it before the detail query.
    publish_builtin_revision(job_db, WORKSPACE_ID)
    job_detail = job_queries.detail(JOB_ID)
    assert job_detail["job"]["storage_dir"] == str(resolved_job_dir)
    assert job_detail["artifacts"] == ["result.json"]
    assert len(job_detail["runs"]) == 1
    resolved_run = job_detail["runs"][0]
    assert resolved_run["log_path"] == str(resolved_node_log)
    assert resolved_run["run_dir"] == str(resolved_run_dir)
    assert resolved_run["session_dir"] == str(resolved_run_dir / "session")
