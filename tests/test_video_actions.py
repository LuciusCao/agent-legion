from server.app.agents import AgentStatusManager
from server.app.pipeline.phases import KNOWLEDGE_PHASES, QUESTION_PHASES
from server.app.services.video_actions import can_rerun_from, rerun_video_record


class TestCanRerunFrom:
    def test_completed_can_rerun_any_phase(self, db):
        video = db.create_video(
            "https://example.com/k1.mp4",
            content_type="knowledge",
            external_id="K001",
        )
        db.update_video(video["id"], status="completed", current_phase="assemble")
        video = db.get_video(video["id"])

        for phase in KNOWLEDGE_PHASES:
            assert can_rerun_from(video, phase) is True

    def test_current_phase_at_or_after_selected(self, db):
        video = db.create_video(
            "https://example.com/k2.mp4",
            content_type="knowledge",
            external_id="K002",
        )
        db.update_video(video["id"], current_phase="chapter_generate", status="failed")
        video = db.get_video(video["id"])

        current_index = KNOWLEDGE_PHASES.index("chapter_generate")
        for phase in KNOWLEDGE_PHASES:
            phase_index = KNOWLEDGE_PHASES.index(phase)
            if phase_index <= current_index:
                assert can_rerun_from(video, phase) is True
            else:
                assert can_rerun_from(video, phase) is False

    def test_question_type_skips_interaction_generate(self, db):
        video = db.create_video(
            "https://example.com/q1.mp4",
            content_type="question",
            external_id="Q001",
        )
        db.update_video(video["id"], status="failed", current_phase="assemble")
        video = db.get_video(video["id"])

        for phase in QUESTION_PHASES:
            assert can_rerun_from(video, phase) is True

        assert can_rerun_from(video, "interaction_generate") is False
        assert can_rerun_from(video, "content_review") is False

    def test_waiting_for_url_cannot_rerun(self, db):
        video = db.create_video(
            "",
            content_type="knowledge",
            external_id="K003",
        )
        # current_phase should be waiting_for_url and status missing_url
        video = db.get_video(video["id"])
        assert video["current_phase"] == "waiting_for_url"

        for phase in KNOWLEDGE_PHASES:
            assert can_rerun_from(video, phase) is False


class TestRerunVideoRecordSkipped:
    def test_returns_skipped_when_cannot_rerun(self, db, settings, tmp_path):
        video = db.create_video(
            "https://example.com/v1.mp4",
            content_type="knowledge",
            external_id="K004",
        )
        db.update_video(video["id"], status="failed", current_phase="transcribe")

        video_dir = settings.videos_dir / video["id"]
        video_dir.mkdir(parents=True, exist_ok=True)

        result = rerun_video_record(db, settings, video["id"], "assemble")

        assert result["status"] == "skipped"
        assert "transcribe" in result["message"]
        assert "assemble" in result["message"]


class TestRerunVideoRecordBusy:
    def test_returns_busy_when_agent_manager_reports_busy_and_db_confirms(self, db, settings):
        video = db.create_video(
            "https://example.com/v1.mp4",
            content_type="knowledge",
            external_id="K005",
        )
        # Simulate a genuinely running video
        db.start_phase(video["id"], "download", ["python3", "download.py"])
        db.update_video(video["id"], status="running", current_phase="download")

        agent_manager = AgentStatusManager()
        agent_manager.set_busy("main", video["id"])

        result = rerun_video_record(db, settings, video["id"], "download", agent_manager)

        assert result["status"] == "busy"
        assert "currently being processed" in result["message"]

    def test_cleans_stale_busy_entry_and_allows_rerun(self, db, settings):
        """Regression test for issue 004: stale _busy_video_ids should not block rerun."""
        video = db.create_video(
            "https://example.com/v1.mp4",
            content_type="knowledge",
            external_id="K006",
        )
        db.update_video(video["id"], status="failed", current_phase="subtitle_review")

        video_dir = settings.videos_dir / video["id"]
        video_dir.mkdir(parents=True, exist_ok=True)

        agent_manager = AgentStatusManager()
        # Simulate stale entry (worker crashed before set_idle was called)
        agent_manager._busy_video_ids.add(video["id"])

        result = rerun_video_record(db, settings, video["id"], "subtitle_review", agent_manager)

        # Should succeed because database says the video is not running
        assert result["status"] == "rerun"
        assert result["phase"] == "subtitle_review"
        # Stale entry should be cleaned up
        assert video["id"] not in agent_manager._busy_video_ids

    def test_returns_busy_when_db_has_running_phase_run(self, db, settings):
        video = db.create_video(
            "https://example.com/v1.mp4",
            content_type="knowledge",
            external_id="K007",
        )
        # Create a running phase_run without updating video status
        db.start_phase(video["id"], "download", ["python3", "download.py"])

        agent_manager = AgentStatusManager()
        agent_manager.set_busy("main", video["id"])

        result = rerun_video_record(db, settings, video["id"], "download", agent_manager)

        assert result["status"] == "busy"
        assert "currently being processed" in result["message"]
