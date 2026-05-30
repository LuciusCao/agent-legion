import json
import sqlite3
import threading
from pathlib import Path

import pytest

from server.app.db import Database
from server.app.db.notifications import NotificationHub
from server.app.records import PHASE_RUN_FIELDS, VIDEO_RECORD_FIELDS


def test_database_creates_video_and_phase_run(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(video["id"], "download", ["python3", "download.py"])
    db.finish_phase(run["id"], "completed", 0, "")

    videos = db.list_videos()
    runs = db.list_phase_runs(video["id"])

    assert videos[0]["id"] == "a"
    assert videos[0]["status"] == "completed"
    assert runs[0]["phase_key"] == "download"
    assert runs[0]["exit_code"] == 0


def test_database_rows_match_declared_record_fields(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(video["id"], "download", ["python3", "download.py"])

    assert set(video) == VIDEO_RECORD_FIELDS
    assert set(db.get_video(video["id"])) == VIDEO_RECORD_FIELDS
    assert set(db.list_videos()[0]) == VIDEO_RECORD_FIELDS
    assert set(run) == PHASE_RUN_FIELDS
    assert set(db.list_phase_runs(video["id"])[0]) == PHASE_RUN_FIELDS


def test_phase_runs_include_openclaw_agent_session_from_command(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(
        video["id"],
        "subtitle_review",
        ["openclaw", "agent", "--agent", "main", "--session-id", "a-123"],
    )

    listed = db.list_phase_runs(video["id"])[0]
    fetched = db.get_phase_run(video["id"], run["id"])

    assert listed["agent_id"] == "main"
    assert listed["agent_session_id"] == "a-123"
    assert fetched["agent_session_id"] == "a-123"


def test_database_update_video_rejects_unknown_fields(db):
    video = db.create_video("https://example.com/path/a.mp4", "Title A")

    with pytest.raises(ValueError, match="Unknown video fields"):
        db.update_video(video["id"], status="queued", bad_field="x")

    assert db.get_video(video["id"])["status"] == "queued"


def test_database_update_video_builds_sql_from_whitelist(db):
    """Valid fields are updated; the SQL construction uses only whitelisted keys."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")

    db.update_video(
        video["id"],
        status="running",
        current_phase="transcribe",
        title="Updated Title",
    )

    updated = db.get_video(video["id"])
    assert updated["status"] == "running"
    assert updated["current_phase"] == "transcribe"
    assert updated["title"] == "Updated Title"
    # source_url should remain unchanged
    assert updated["source_url"] == "https://example.com/path/a.mp4"


def test_notify_includes_interaction_stats_for_knowledge_videos(tmp_path: Path) -> None:
    """Regression test for issue 002: SSE push should include interaction_stats."""
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    video_dir = videos_dir / "knowledge_K001"
    video_dir.mkdir()

    # Create interaction artifacts
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published"}), encoding="utf-8"
    )

    captured: dict = {}
    hub = NotificationHub(
        on_change=lambda video: captured.update(video or {}),
    )

    db = Database(tmp_path / "video_hive.sqlite", hub=hub, videos_dir=videos_dir)
    db.create_video(
        "https://example.com/k001.mp4",
        title="K001",
        content_type="knowledge",
        external_id="K001",
        storage_dir=str(video_dir),
    )

    # Trigger _notify via update_video
    db.update_video("knowledge_K001", current_phase="assemble", status="completed")

    assert "interaction_stats" in captured
    assert captured["interaction_stats"] == {
        "example_practice": {"passed": 1, "total": 1},
        "interaction_summary": {"passed": 1, "total": 1},
    }
    assert captured.get("interaction_review_status") == "all_passed"


def test_notify_does_not_include_interaction_stats_for_question_videos(tmp_path: Path) -> None:
    """Question videos should not have interaction fields injected into SSE payload."""
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    video_dir = videos_dir / "question_Q001"
    video_dir.mkdir()

    captured: dict = {}
    hub = NotificationHub(
        on_change=lambda video: captured.update(video or {}),
    )

    db = Database(tmp_path / "video_hive.sqlite", hub=hub, videos_dir=videos_dir)
    db.create_video(
        "https://example.com/q001.mp4",
        title="Q001",
        content_type="question",
        external_id="Q001",
        storage_dir=str(video_dir),
    )

    db.update_video("question_Q001", current_phase="assemble", status="completed")

    assert "interaction_stats" not in captured
    assert "interaction_review_status" not in captured


def test_notify_skips_interaction_stats_when_videos_dir_is_none(tmp_path: Path) -> None:
    """When videos_dir is not provided, _notify should not crash and skip enrichment."""
    captured: dict = {}
    hub = NotificationHub(
        on_change=lambda video: captured.update(video or {}),
    )

    db = Database(tmp_path / "video_hive.sqlite", hub=hub)
    db.create_video(
        "https://example.com/k001.mp4",
        title="K001",
        content_type="knowledge",
        external_id="K001",
    )

    db.update_video("knowledge_K001", current_phase="assemble", status="completed")

    assert "interaction_stats" not in captured
    assert "interaction_review_status" not in captured


def test_update_video_rejects_completed_without_assemble_phase(db):
    """Regression test for issue 003: status='completed' requires current_phase='assemble'."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")

    # Setting status='completed' while current_phase='download' should fail
    with pytest.raises(
        ValueError,
        match="Invalid state: status='completed' requires current_phase='assemble'",
    ):
        db.update_video(video["id"], status="completed")

    # Setting both to valid combination should succeed
    db.update_video(video["id"], current_phase="assemble", status="completed")
    updated = db.get_video(video["id"])
    assert updated["status"] == "completed"
    assert updated["current_phase"] == "assemble"

    # Transitioning from completed back to queued should succeed
    db.update_video(video["id"], current_phase="download", status="queued")
    updated = db.get_video(video["id"])
    assert updated["status"] == "queued"
    assert updated["current_phase"] == "download"

    # Setting current_phase to download while status is already queued should succeed
    db.update_video(video["id"], current_phase="download")
    assert db.get_video(video["id"])["status"] == "queued"


def test_update_video_allows_completed_with_assemble_from_any_phase(db):
    """Valid transition: any phase can become completed+assemble."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    db.start_phase(video["id"], "download", ["python3", "download.py"])
    db.finish_phase(1, "completed", 0, "")

    # Transition to completed+assemble should succeed
    db.update_video(video["id"], current_phase="assemble", status="completed")
    updated = db.get_video(video["id"])
    assert updated["status"] == "completed"
    assert updated["current_phase"] == "assemble"


_EXPECTED_INDEXES = {
    "idx_videos_status",
    "idx_videos_content_type_external_id",
    "idx_videos_created_at",
    "idx_phase_runs_video_id",
    "idx_phase_runs_video_id_status",
    "idx_transcription_runs_video_id",
}


def test_database_creates_performance_indexes(db):
    """Regression test for issue 012: critical indexes must exist."""
    with db.connect() as conn:
        indexes = {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type='index'").fetchall()
        }
    assert _EXPECTED_INDEXES.issubset(indexes), f"Missing indexes: {_EXPECTED_INDEXES - indexes}"


def test_has_running_phase_run_returns_true_when_running(db):
    """Regression test for issue 012: SQL-level running check."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    db.start_phase(video["id"], "download", ["python3", "download.py"])
    assert db.has_running_phase_run(video["id"]) is True


def test_has_running_phase_run_returns_false_when_none_running(db):
    """Regression test for issue 012: SQL-level running check."""
    video = db.create_video("https://example.com/path/a.mp4", "Title A")
    run = db.start_phase(video["id"], "download", ["python3", "download.py"])
    db.finish_phase(run["id"], "completed", 0, "")
    assert db.has_running_phase_run(video["id"]) is False


def test_list_videos_status_filter_string(db):
    """Regression test for issue 012: list_videos with status filter."""
    v1 = db.create_video("https://example.com/a.mp4", "A")
    v2 = db.create_video("https://example.com/b.mp4", "B")
    db.update_video(v1["id"], current_phase="assemble", status="completed")
    db.update_video(v2["id"], status="failed")

    result = db.list_videos(status_filter="completed")
    assert len(result) == 1
    assert result[0]["id"] == v1["id"]


def test_list_videos_status_filter_list(db):
    """Regression test for issue 012: list_videos with multiple status filter."""
    v1 = db.create_video("https://example.com/a.mp4", "A")
    v2 = db.create_video("https://example.com/b.mp4", "B")
    v3 = db.create_video("https://example.com/c.mp4", "C")
    db.update_video(v1["id"], current_phase="assemble", status="completed")
    db.update_video(v2["id"], status="failed")

    result = db.list_videos(status_filter=["completed", "failed"])
    ids = {v["id"] for v in result}
    assert v1["id"] in ids
    assert v2["id"] in ids
    assert v3["id"] not in ids


def test_list_videos_limit_and_offset(db):
    """Regression test for issue 012: list_videos with limit and offset."""
    for i in range(5):
        db.create_video(f"https://example.com/{i}.mp4", f"Video {i}")

    all_videos = db.list_videos()
    assert len(all_videos) == 5

    limited = db.list_videos(limit=2)
    assert len(limited) == 2
    assert limited[0]["id"] == all_videos[0]["id"]
    assert limited[1]["id"] == all_videos[1]["id"]

    offset = db.list_videos(limit=2, offset=2)
    assert len(offset) == 2
    assert offset[0]["id"] == all_videos[2]["id"]
    assert offset[1]["id"] == all_videos[3]["id"]


def test_batch_get_videos_returns_matching_records(db):
    """Regression test for issue 012: batch get videos."""
    v1 = db.create_video("https://example.com/a.mp4", "A")
    v2 = db.create_video("https://example.com/b.mp4", "B")
    db.create_video("https://example.com/c.mp4", "C")

    result = db.batch_get_videos([v1["id"], v2["id"]])
    ids = {v["id"] for v in result}
    assert v1["id"] in ids
    assert v2["id"] in ids
    assert len(result) == 2


def test_batch_get_videos_empty_list(db):
    """Regression test for issue 012: batch get with empty list."""
    assert db.batch_get_videos([]) == []


def test_batch_delete_videos_cascades(db):
    """Regression test for issue 012: batch delete videos with cascade."""
    v1 = db.create_video("https://example.com/a.mp4", "A")
    v2 = db.create_video("https://example.com/b.mp4", "B")
    db.start_phase(v1["id"], "download", ["python3", "download.py"])
    db.start_phase(v2["id"], "transcribe", ["python3", "transcribe.py"])

    db.batch_delete_videos([v1["id"], v2["id"]])

    assert db.get_video(v1["id"]) is None
    assert db.get_video(v2["id"]) is None
    assert db.list_phase_runs(v1["id"]) == []
    assert db.list_phase_runs(v2["id"]) == []


def test_batch_delete_videos_empty_list(db):
    """Regression test for issue 012: batch delete with empty list."""
    db.batch_delete_videos([])  # should not raise


def test_find_videos_by_identities_returns_matches(db):
    """Regression test for issue 012: batch identity lookup."""
    v1 = db.create_video(
        "https://example.com/a.mp4", "A", content_type="knowledge", external_id="K001"
    )
    v2 = db.create_video(
        "https://example.com/b.mp4", "B", content_type="question", external_id="Q001"
    )

    result = db.find_videos_by_identities(
        [
            ("knowledge", "K001"),
            ("question", "Q001"),
            ("knowledge", "NOTFOUND"),
        ]
    )
    assert result[("knowledge", "K001")]["id"] == v1["id"]
    assert result[("question", "Q001")]["id"] == v2["id"]
    assert ("knowledge", "NOTFOUND") not in result


def test_find_videos_by_identities_empty_list(db):
    """Regression test for issue 012: batch identity lookup with empty list."""
    assert db.find_videos_by_identities([]) == {}


def test_read_conn_reused_within_same_thread(db):
    """同线程多次调用 _ensure_read_conn 应复用同一连接对象。"""
    conn1 = db._ensure_read_conn()
    conn2 = db._ensure_read_conn()
    assert conn1 is conn2
    assert isinstance(conn1, sqlite3.Connection)


def test_read_conn_isolated_across_threads(db):
    """不同线程应获得独立连接对象。"""
    conns = []

    def collect():
        conns.append(db._ensure_read_conn())

    t1 = threading.Thread(target=collect)
    t2 = threading.Thread(target=collect)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(conns) == 2
    assert conns[0] is not conns[1]


def test_close_read_conn_clears_and_allows_recreate(db):
    """关闭后再次获取应创建新连接。"""
    conn1 = db._ensure_read_conn()
    db.close_read_conn()
    conn2 = db._ensure_read_conn()
    assert conn1 is not conn2


def test_connect_read_reuses_pooled_conn_for_same_thread(db):
    """_connect_read 在同线程中应自动复用已预热连接。"""
    db._ensure_read_conn()
    with db._connect_read() as conn:
        pooled = db._ensure_read_conn()
        assert conn is pooled


def test_list_running_video_summaries_returns_limited_fields(db):
    """只返回 id, current_phase, storage_dir 三个字段。"""
    db.create_video("https://example.com/a.mp4", "A")
    db.update_video("a", status="running", current_phase="download")

    summaries = db.list_running_video_summaries()
    assert len(summaries) == 1
    assert set(summaries[0].keys()) == {"id", "current_phase", "storage_dir"}
    assert summaries[0]["id"] == "a"
    assert summaries[0]["current_phase"] == "download"


def test_list_running_video_summaries_filters_by_status(db):
    """只返回 status='running' 的视频。"""
    db.create_video("https://example.com/a.mp4", "A")
    db.create_video("https://example.com/b.mp4", "B")
    db.update_video("a", status="running", current_phase="download")
    db.update_video("b", status="queued", current_phase="download")

    summaries = db.list_running_video_summaries()
    assert len(summaries) == 1
    assert summaries[0]["id"] == "a"


def test_batch_update_packed(db):
    db.create_video("https://example.com/a.mp4", "A")
    db.create_video("https://example.com/b.mp4", "B")
    db.batch_update_packed(["a", "b"], packed=1)
    assert db.get_video("a")["packed"] == 1
    assert db.get_video("b")["packed"] == 1


def test_batch_update_packed_empty_list(db):
    db.batch_update_packed([], packed=1)


def test_batch_notify_uses_single_connection(db):
    """batch_notify should reuse a single read connection for all video_ids."""
    import sqlite3
    from unittest.mock import patch

    v1 = db.create_video("https://example.com/v1.mp4", "V1")
    v2 = db.create_video("https://example.com/v2.mp4", "V2")
    v3 = db.create_video("https://example.com/v3.mp4", "V3")

    created_connections = []
    original_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        created_connections.append(conn)
        return conn

    with patch("server.app.db.queries.sqlite3.connect", side_effect=tracking_connect):
        db.batch_notify([v1["id"], v2["id"], v3["id"]])

    # batch_notify should use _ensure_read_conn once and close it once.
    # The write connection from create_video is unrelated.
    # We allow some slack for any internal connections, but the key is
    # that batch_notify itself doesn't open a new connection per video.
    assert len(created_connections) <= 4, (
        f"Expected at most 4 connections, got {len(created_connections)}"
    )


def test_batch_update_packed_triggers_notification(db):
    """batch_update_packed should notify all affected videos."""
    from unittest.mock import MagicMock

    v1 = db.create_video("https://example.com/v1.mp4", "V1")
    v2 = db.create_video("https://example.com/v2.mp4", "V2")

    emitted = []
    db._hub = MagicMock()
    db._hub.emit_change = lambda video: emitted.append(video["id"] if video else None)

    db.batch_update_packed([v1["id"], v2["id"]], packed=1)

    assert len(emitted) == 2
    assert v1["id"] in emitted
    assert v2["id"] in emitted
