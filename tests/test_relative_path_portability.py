import json
import shutil
from pathlib import Path

import pytest

from server.app.db import Database
from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.pipeline.common import resolve_video_dir
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_logs import JobLogService
from server.app.services.job_queries import JobQueryService
from server.app.services.package_deletion import PackageDeletionService
from server.app.services.video_read import VideoReadService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import load_settings
from server.app.storage_paths import resolve_data_path, resolve_job_dir
from tests.postgres_support import TEST_DATABASE_URL

VIDEO_ID = "knowledge_test123"
WORKSPACE_ID = "default"
WORKFLOW_KEY = "question_comprehension_info"
SOURCE_ID = "S123"
JOB_ID = f"{WORKSPACE_ID}_{WORKFLOW_KEY}_{SOURCE_ID}"
NODE_KEY = "fetch_questions"
RUN_TOKEN = "run-abc"
PACKAGE_NAME = "batch.zip"


def _seed_old_root(old_root: Path) -> None:
    """Create files and a database under *old_root* with canonical relative paths."""
    db_path = TEST_DATABASE_URL
    init_db(db_path)

    # Managed directories (mirroring the canonical relative values stored below).
    video_dir = old_root / "videos" / VIDEO_ID
    job_dir = old_root / "jobs" / WORKSPACE_ID / JOB_ID
    log_dir = old_root / "logs"
    job_log_dir = old_root / "logs" / "jobs"
    package_dir = old_root / "packages"

    video_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    job_log_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)

    # Video artifacts used by the detail API / video file route.
    (video_dir / f"{VIDEO_ID}.mp4").write_bytes(b"fake video bytes")
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {"interactions": [{"id": "i1", "type": "click"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published", "reviews": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    # Job artifact used by JobArtifactService / job artifact route.
    (job_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")

    # Run/session directories used by JobQueryService path projection.
    session_dir = job_dir / "runs" / "node" / RUN_TOKEN / "session"
    session_dir.mkdir(parents=True)
    (session_dir / ".keep").write_text("", encoding="utf-8")

    # Logs used by the video logs route and JobLogService.
    (log_dir / f"{VIDEO_ID}-download.log").write_text(
        "download complete from old_root\n", encoding="utf-8"
    )
    (job_log_dir / f"{JOB_ID}-{NODE_KEY}.log").write_text(
        "node run complete from old_root\n", encoding="utf-8"
    )

    # Package used by PackageDeletionService.
    (package_dir / PACKAGE_NAME).write_bytes(b"fake package bytes")

    with write_transaction(db_path) as conn:
        conn.execute(
            """
            insert into workspaces(id, name, default_workflow_key, default_entity)
            values ('default', 'Default', 'question_comprehension_info', 'question')
            """
        )
        conn.execute(
            """
            insert into videos(
                id, source_url, title, content_type, external_id,
                knowledge_code, storage_dir, current_phase, status
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                VIDEO_ID,
                "http://example.com/video.mp4",
                "Test Video",
                "knowledge",
                "test123",
                "test123",
                f"videos/{VIDEO_ID}",
                "assemble",
                "completed",
            ),
        )
        conn.execute(
            """
            insert into phase_runs(video_id, phase_key, status, log_path)
            values (?, ?, ?, ?)
            """,
            (VIDEO_ID, "download", "completed", f"logs/{VIDEO_ID}-download.log"),
        )
        conn.execute(
            """
            insert into jobs(
                id, workspace_id, workflow_key, source_type, source_id,
                batch_id, title, storage_dir, status
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            values (?, ?, ?, ?, ?, ?)
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
        conn.execute(
            """
            insert into packages(path, name, video_count, size_bytes, locked)
            values (?, ?, ?, ?, ?)
            """,
            (f"packages/{PACKAGE_NAME}", "batch", 1, 100, 0),
        )


@pytest.fixture
def portable_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Return *(old_root, new_root)* with a copied, portable Video Hive tree."""
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
    db = Database(TEST_DATABASE_URL, videos_dir=settings.videos_dir)
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=settings.jobs_dir)

    # ------------------------------------------------------------------
    # 1. Database values are still the original canonical relative paths.
    # ------------------------------------------------------------------
    video = db.get_video(VIDEO_ID)
    assert video is not None
    assert video["storage_dir"] == f"videos/{VIDEO_ID}"

    phase_runs = db.list_phase_runs(VIDEO_ID)
    assert len(phase_runs) == 1
    assert phase_runs[0]["log_path"] == f"logs/{VIDEO_ID}-download.log"

    job = job_db.get_job(JOB_ID)
    assert job is not None
    assert job["storage_dir"] == f"jobs/{WORKSPACE_ID}/{JOB_ID}"

    node_runs = job_db.list_node_runs(JOB_ID)
    assert len(node_runs) == 1
    run = node_runs[0]
    assert run["log_path"] == f"logs/jobs/{JOB_ID}-{NODE_KEY}.log"
    assert run["run_dir"] == f"jobs/{WORKSPACE_ID}/{JOB_ID}/runs/node/{RUN_TOKEN}"
    assert run["session_dir"] == f"jobs/{WORKSPACE_ID}/{JOB_ID}/runs/node/{RUN_TOKEN}/session"

    packages = db.list_packages(limit=10)
    assert len(packages) == 1
    assert packages[0]["path"] == f"packages/{PACKAGE_NAME}"

    # ------------------------------------------------------------------
    # 2. Low-level resolvers point under *new_root*.
    # ------------------------------------------------------------------
    resolved_video_dir = resolve_video_dir(video, settings.videos_dir)
    assert resolved_video_dir == (settings.videos_dir / VIDEO_ID).resolve()
    assert resolved_video_dir.is_relative_to(new_root.resolve())

    resolved_job_dir = resolve_job_dir(job, settings.jobs_dir)
    assert resolved_job_dir == (settings.jobs_dir / WORKSPACE_ID / JOB_ID).resolve()
    assert resolved_job_dir.is_relative_to(new_root.resolve())

    resolved_video_log = resolve_data_path(
        phase_runs[0]["log_path"], settings.data_dir, allow_missing=True
    )
    assert resolved_video_log == (settings.logs_dir / f"{VIDEO_ID}-download.log").resolve()

    resolved_node_log = resolve_data_path(run["log_path"], settings.data_dir, allow_missing=True)
    assert resolved_node_log == (settings.logs_dir / "jobs" / f"{JOB_ID}-{NODE_KEY}.log").resolve()

    resolved_package = resolve_data_path(packages[0]["path"], settings.data_dir, allow_missing=True)
    assert resolved_package == (settings.packages_dir / PACKAGE_NAME).resolve()

    resolved_run_dir = resolve_data_path(run["run_dir"], settings.data_dir, allow_missing=True)
    assert (
        resolved_run_dir
        == (settings.jobs_dir / WORKSPACE_ID / JOB_ID / "runs" / "node" / RUN_TOKEN).resolve()
    )

    # ------------------------------------------------------------------
    # 3. Service reads resolve to *new_root*.
    # ------------------------------------------------------------------
    read_service = VideoReadService(db, settings)
    detail = read_service.get_video_detail(VIDEO_ID)
    assert detail is not None
    assert detail["interaction_stats"] == {"click": {"passed": 1, "total": 1}}

    artifact_service = JobArtifactService(job_db)
    artifact = artifact_service.read(JOB_ID, "result.json")
    assert artifact["content"] == '{"ok": true}'

    log_service = JobLogService(settings, job_db)
    log_result = log_service.read(JOB_ID, int(run["id"]))
    assert "old_root" in log_result["log"]

    workflows = WorkflowCatalogService(settings)
    workspace_executor_config = WorkspaceExecutorConfigurationService(job_db)
    job_queries = JobQueryService(job_db, settings, workflows, workspace_executor_config)
    job_detail = job_queries.detail(JOB_ID)
    assert job_detail["job"]["storage_dir"] == str(resolved_job_dir)
    assert job_detail["artifacts"] == ["result.json"]
    assert len(job_detail["runs"]) == 1
    resolved_run = job_detail["runs"][0]
    assert resolved_run["log_path"] == str(resolved_node_log)
    assert resolved_run["run_dir"] == str(resolved_run_dir)
    assert resolved_run["session_dir"] == str(resolved_run_dir / "session")

    package_deletion = PackageDeletionService(db, settings.packages_dir)
    package_id = int(packages[0]["id"])
    assert (settings.packages_dir / PACKAGE_NAME).exists()
    package_deletion.delete(package_id)
    assert not (settings.packages_dir / PACKAGE_NAME).exists()
    assert db.list_packages(limit=10) == []
