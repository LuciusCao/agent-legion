from server.app.services.intake import add_video_items, normalized_content_type
from server.app.services.video_actions import (
    batch_rerun_video_records,
    delete_video_record,
    select_videos_for_package,
)
from tests.conftest import InputItem


def test_intake_normalizes_unknown_content_type_and_creates_storage(db, settings):
    result = add_video_items(
        db,
        settings,
        [InputItem(url="https://example.com/course/g1.mp4", content_type="unknown")],
    )

    video = result["videos"][0]
    assert normalized_content_type("unknown") == "knowledge"
    assert video["id"] == "g1"
    assert video["content_type"] == "knowledge"
    assert (settings.data_dir / "videos" / "g1").is_dir()


def test_batch_rerun_uses_same_normalization_as_single_rerun(db, settings):
    db.create_video("https://example.com/q1.mp4", content_type="question", external_id="Q001")

    results = batch_rerun_video_records(
        db,
        settings,
        ["question_Q001", "missing"],
        "content_review",
    )

    assert results == [
        {"video_id": "question_Q001", "status": "rerun", "phase": "assemble", "message": ""},
        {"video_id": "missing", "status": "not_found", "phase": "content_review", "message": "Video not found"},
    ]
    assert db.get_video("question_Q001")["current_phase"] == "assemble"


def test_delete_video_record_removes_storage_and_package_selection_defaults(db, settings):
    completed = db.create_video(
        "https://example.com/k1.mp4",
        content_type="knowledge",
        external_id="K001",
    )
    queued = db.create_video(
        "https://example.com/k2.mp4",
        content_type="knowledge",
        external_id="K002",
    )
    db.update_video(completed["id"], status="completed")
    storage_dir = settings.data_dir / "videos" / completed["id"]
    storage_dir.mkdir(parents=True)
    db.update_video(completed["id"], storage_dir=str(storage_dir))

    default_selection = select_videos_for_package(db)
    explicit_selection = select_videos_for_package(db, [queued["id"], "missing"])

    assert [video["id"] for video in default_selection.videos] == [completed["id"]]
    assert default_selection.missing_ids == []
    assert [video["id"] for video in explicit_selection.videos] == [queued["id"]]
    assert explicit_selection.missing_ids == ["missing"]
    assert delete_video_record(db, settings, completed["id"]) is True
    assert db.get_video(completed["id"]) is None
    assert not storage_dir.exists()
