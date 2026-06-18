import json
from pathlib import Path

from server.app.db import Database
from server.app.services.interaction_cache import InteractionCacheService
from server.app.services.video_read import VideoReadService


def test_video_read_service_enriches_detail_without_update(tmp_path: Path, db: Database, settings):
    video_id = "knowledge_K001"
    db.create_video(
        "https://example.com/k1.mp4",
        content_type="knowledge",
        external_id="K001",
    )
    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"id": "n1", "type": "example_practice"}]}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published", "reviews": []}),
        encoding="utf-8",
    )

    service = VideoReadService(db, settings)
    calls = []
    original_update_video = db.update_video

    def tracking_update_video(vid, **fields):
        calls.append((vid, fields))
        return original_update_video(vid, **fields)

    db.update_video = tracking_update_video

    result = service.get_video_detail(video_id)
    assert result is not None
    assert result["interaction_stats"] == {"example_practice": {"passed": 1, "total": 1}}
    assert result["interaction_review_status"] == "all_passed"
    assert not calls, "VideoReadService.get_video_detail must not call db.update_video"


def test_video_read_service_enriches_list_without_update(tmp_path: Path, db: Database, settings):
    video_id = "knowledge_K001"
    db.create_video(
        "https://example.com/k1.mp4",
        content_type="knowledge",
        external_id="K001",
    )
    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
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

    service = VideoReadService(db, settings)
    calls = []
    original_update_video = db.update_video

    def tracking_update_video(vid, **fields):
        calls.append((vid, fields))
        return original_update_video(vid, **fields)

    db.update_video = tracking_update_video

    videos = service.list_videos()
    assert len(videos) == 1
    assert videos[0]["interaction_stats"] == {
        "example_practice": {"passed": 0, "total": 1},
        "interaction_summary": {"passed": 0, "total": 1},
    }
    assert not calls, "VideoReadService.list_videos must not call db.update_video"


def test_video_read_service_returns_none_for_missing_video(db: Database, settings):
    service = VideoReadService(db, settings)
    assert service.get_video_detail("missing") is None


def test_interaction_cache_service_refresh_persists_stats(tmp_path: Path, db: Database, settings):
    video_id = "knowledge_K001"
    db.create_video(
        "https://example.com/k1.mp4",
        content_type="knowledge",
        external_id="K001",
    )
    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
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
        json.dumps(
            {
                "status": "published",
                "reviews": [
                    {"item_id": "n1", "status": "published"},
                    {"item_id": "n2", "status": "rejected"},
                ],
            }
        ),
        encoding="utf-8",
    )

    service = InteractionCacheService(db, settings)
    service.refresh(video_id)

    video = db.get_video(video_id)
    assert video is not None
    cached = json.loads(video["interaction_stats_json"])
    assert cached["example_practice"] == {"passed": 1, "total": 1}
    assert cached["interaction_summary"] == {"passed": 0, "total": 1}
    assert video["interaction_review_status"] == "partial"


def test_interaction_cache_service_refresh_ignores_non_knowledge(
    tmp_path: Path, db: Database, settings
):
    video_id = "question_Q001"
    db.create_video(
        "https://example.com/q1.mp4",
        content_type="question",
        external_id="Q001",
    )

    service = InteractionCacheService(db, settings)
    service.refresh(video_id)

    video = db.get_video(video_id)
    assert video is not None
    assert video["interaction_stats_json"] == ""
    assert video["interaction_review_status"] == ""
