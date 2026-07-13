from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db
from server.app.jobs import JobQueries
from server.app.pipeline.common import make_record_id

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "migrate-video-hive-to-agent-legion.py"
)


@pytest.fixture(scope="session")
def migration_module() -> Any:
    name = "migrate_video_hive_to_agent_legion"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _make_environment(migration_module: Any, tmp_path: Path) -> Any:
    root = tmp_path / "project"
    data_dir = root / "data"
    return migration_module.Environment(
        db_path=data_dir / "video_hive.sqlite",
        data_dir=data_dir,
        videos_dir=data_dir / "videos",
        jobs_dir=data_dir / "jobs",
        backup_dir=data_dir / "backups",
        root_dir=Path(__file__).resolve().parents[1],
    )


def create_legacy_db_with_video(
    migration_module: Any,
    tmp_path: Path,
    *,
    content_type: str = "knowledge",
    external_id: str = "K001",
    status: str = "completed",
    current_phase: str = "assemble",
) -> Any:
    env = _make_environment(migration_module, tmp_path)
    init_db(env.db_path)

    source_url = "" if status == "missing_url" else "http://example.com/video"
    title = f"Test {external_id}"
    video_id = make_record_id(source_url, content_type, external_id)
    knowledge_code = external_id if content_type == "knowledge" else ""
    question_id = external_id if content_type == "question" else ""

    with closing(connect_sqlite(env.db_path)) as conn, conn:
        conn.execute(
            """
            insert into videos(
              id, source_url, title, content_type, external_id, knowledge_code,
              question_id, source_uuid, storage_dir, current_phase, status, duration
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                source_url,
                title,
                content_type,
                external_id,
                knowledge_code,
                question_id,
                "",
                "",
                current_phase,
                status,
                42.0,
            ),
        )

    video_dir = env.videos_dir / video_id
    video_dir.mkdir(parents=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
    )
    (video_dir / f"{video_id}.mp4").write_bytes(b"legacy mp4")

    if status == "completed":
        (video_dir / "transcription.json").write_text("{}", encoding="utf-8")
        (video_dir / "subtitles_reviewed.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
        )
        (video_dir / "subtitle_review_report.json").write_text("{}", encoding="utf-8")
        (video_dir / "chapters_raw.json").write_text("[]", encoding="utf-8")
        (video_dir / "chapters.json").write_text("[]", encoding="utf-8")
        (video_dir / "interactions.json").write_text("[]", encoding="utf-8")
        (video_dir / "checklist.json").write_text("{}", encoding="utf-8")
        (video_dir / "review_result.json").write_text("{}", encoding="utf-8")
        (video_dir / "metadata.json").write_text("{}", encoding="utf-8")
        (video_dir / "report.md").write_text("# Report\n", encoding="utf-8")
        (video_dir / "upload_params.json").write_text("{}", encoding="utf-8")

    if status in ("completed", "failed"):
        with closing(connect_sqlite(env.db_path)) as conn, conn:
            conn.execute(
                """
                insert into phase_runs(video_id, phase_key, status, exit_code, error_message)
                values (?, ?, ?, ?, ?)
                """,
                (video_id, current_phase, status, 0 if status == "completed" else 1, ""),
            )

    return env


def create_legacy_db_with_completed_knowledge_video(migration_module: Any, tmp_path: Path) -> Any:
    return create_legacy_db_with_video(
        migration_module,
        tmp_path,
        content_type="knowledge",
        external_id="K001",
        status="completed",
        current_phase="assemble",
    )


class TestPreflight:
    def test_preflight_blocks_question_videos(self, migration_module: Any, tmp_path: Path) -> None:
        env = create_legacy_db_with_video(migration_module, tmp_path, content_type="question")
        report = migration_module.preflight(env)
        assert report.blocked
        assert any("question" in error.message for error in report.errors)

    def test_preflight_blocks_target_job_dir_conflict(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = create_legacy_db_with_video(
            migration_module, tmp_path, content_type="knowledge", external_id="K001"
        )
        (env.jobs_dir / "video-K001").mkdir(parents=True)
        report = migration_module.preflight(env)
        assert report.blocked
        assert any("target job directory" in error.message for error in report.errors)

    def test_preflight_blocks_running_videos(self, migration_module: Any, tmp_path: Path) -> None:
        env = create_legacy_db_with_video(
            migration_module, tmp_path, status="running", current_phase="download"
        )
        report = migration_module.preflight(env)
        assert report.blocked
        assert any("running" in error.message for error in report.errors)

    def test_preflight_blocks_unknown_phase(self, migration_module: Any, tmp_path: Path) -> None:
        env = create_legacy_db_with_video(
            migration_module, tmp_path, status="queued", current_phase="obsolete"
        )
        report = migration_module.preflight(env)
        assert report.blocked
        assert any("phase" in error.message.lower() for error in report.errors)

    def test_preflight_allows_valid_knowledge_video(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)
        report = migration_module.preflight(env)
        assert not report.blocked

    def test_preflight_blocks_duplicate_target_job_ids(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = _make_environment(migration_module, tmp_path)
        init_db(env.db_path)
        with closing(connect_sqlite(env.db_path)) as conn, conn:
            for video_id, external_id in (("legacy-one", "K/001"), ("legacy-two", "K 001")):
                conn.execute(
                    """
                    insert into videos(
                      id, source_url, title, content_type, external_id,
                      knowledge_code, current_phase, status
                    )
                    values (?, ?, ?, 'knowledge', ?, ?, 'download', 'queued')
                    """,
                    (
                        video_id,
                        f"https://example.invalid/{video_id}.mp4",
                        video_id,
                        external_id,
                        external_id,
                    ),
                )

        report = migration_module.preflight(env)

        assert report.blocked
        assert any("duplicate target job id" in error.message for error in report.errors)

    def test_preflight_uses_legacy_storage_dir(self, migration_module: Any, tmp_path: Path) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)
        video_id = "knowledge_K001"
        custom_dir = env.videos_dir / "custom" / video_id
        custom_dir.parent.mkdir(parents=True)
        shutil.move(str(env.videos_dir / video_id), custom_dir)
        with closing(connect_sqlite(env.db_path)) as conn, conn:
            conn.execute(
                "update videos set storage_dir=? where id=?",
                ("videos/custom/knowledge_K001", video_id),
            )

        report = migration_module.preflight(env)

        assert not report.blocked

    def test_preflight_blocks_completed_video_missing_required_artifact(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)
        (env.videos_dir / "knowledge_K001" / "metadata.json").unlink()

        report = migration_module.preflight(env)

        assert report.blocked
        assert any("missing completed artifact" in error.message for error in report.errors)


class TestApply:
    def test_apply_copies_artifacts_and_creates_workspace_job(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)
        result = migration_module.apply_migration(env)

        assert not result.blocked
        job_dir = env.jobs_dir / result.mappings[0].job_id
        assert (job_dir / "video_input.json").is_file()
        assert (job_dir / "source.mp4").read_bytes() == b"legacy mp4"
        assert (job_dir / "subtitles.srt").is_file()
        assert (job_dir / "package_manifest.json").is_file()

        queries = JobQueries(env.db_path, env.jobs_dir)
        job = queries.get_job(result.mappings[0].job_id)
        assert job is not None
        assert job["workflow_key"] == "video_knowledge"
        assert job["source_type"] == "video"
        assert queries.list_job_nodes(job["id"])
        assert queries.list_node_runs(job["id"])
        with queries._connect_read() as conn:
            bindings = {
                row["node_key"]: row["executor_id"]
                for row in conn.execute(
                    """
                    select node_key, executor_id from workspace_node_bindings
                    where workspace_id='video_knowledge' and workflow_key='video_knowledge'
                    """
                )
            }
        assert bindings["download"] == "local-default"
        assert bindings["subtitle_review"] == "pi"

    def test_apply_marks_video_workspace_executor_configuration_authoritative(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)

        migration_module.apply_migration(env)

        with closing(connect_sqlite(env.db_path)) as conn, conn:
            row = conn.execute(
                "select workspace_id from workspace_executor_bootstrap_state where workspace_id=?",
                ("video_knowledge",),
            ).fetchone()
        assert row is not None

    def test_apply_copies_artifacts_from_legacy_storage_dir(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)
        video_id = "knowledge_K001"
        custom_dir = env.videos_dir / "custom" / video_id
        custom_dir.parent.mkdir(parents=True)
        shutil.move(str(env.videos_dir / video_id), custom_dir)
        with closing(connect_sqlite(env.db_path)) as conn, conn:
            conn.execute(
                "update videos set storage_dir=? where id=?",
                ("videos/custom/knowledge_K001", video_id),
            )

        result = migration_module.apply_migration(env)

        job_dir = env.jobs_dir / result.mappings[0].job_id
        assert (job_dir / "source.mp4").read_bytes() == b"legacy mp4"
        assert json.loads((job_dir / "metadata.json").read_text(encoding="utf-8")) == {}

    def test_apply_creates_backup_and_report(self, migration_module: Any, tmp_path: Path) -> None:
        env = create_legacy_db_with_completed_knowledge_video(migration_module, tmp_path)
        migration_module.apply_migration(env)
        assert list(env.backup_dir.glob("video-hive-before-agent-legion-*.sqlite"))
        assert list(env.backup_dir.glob("video-hive-to-agent-legion-report-*.json"))

    def test_apply_copy_failure_does_not_write_jobs(
        self, migration_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = create_legacy_db_with_video(
            migration_module, tmp_path, external_id="K001", status="completed"
        )
        create_legacy_db_with_video(
            migration_module, tmp_path, external_id="K002", status="completed"
        )
        real_copytree = shutil.copytree
        calls = {"count": 0}

        def flaky_copytree(src: Path, dst: Path):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("copy failed")
            return real_copytree(src, dst)

        monkeypatch.setattr(migration_module.shutil, "copytree", flaky_copytree)

        with pytest.raises(OSError, match="copy failed"):
            migration_module.apply_migration(env)

        queries = JobQueries(env.db_path, env.jobs_dir)
        assert queries.list_jobs(workspace_id="video_knowledge") == []
        assert not (env.jobs_dir / "video-K001").exists()
        assert not (env.jobs_dir / "video-K002").exists()


@pytest.mark.parametrize(
    "legacy_status,current_phase,expected_job_status,expected_node",
    [
        ("completed", "assemble", "completed", ("package", "completed")),
        ("failed", "transcribe", "failed", ("transcribe", "failed")),
        ("queued", "chapter_generate", "queued", ("chapter_generate", "pending")),
        ("missing_url", "waiting_for_url", "failed", ("download", "failed")),
    ],
)
def test_status_mapping(
    legacy_status: str,
    current_phase: str,
    expected_job_status: str,
    expected_node: tuple[str, str],
    migration_module: Any,
    tmp_path: Path,
) -> None:
    env = create_legacy_db_with_video(
        migration_module, tmp_path, status=legacy_status, current_phase=current_phase
    )
    result = migration_module.apply_migration(env)
    queries = JobQueries(env.db_path, env.jobs_dir)
    job = queries.get_job(result.mappings[0].job_id)
    assert job is not None
    nodes = {node["node_key"]: node for node in queries.list_job_nodes(job["id"])}

    assert job["status"] == expected_job_status
    assert nodes[expected_node[0]]["status"] == expected_node[1]
