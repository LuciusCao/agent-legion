
from fastapi.testclient import TestClient

from server.app.main import create_app


def test_add_video_list_artifacts_and_rerun(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/course/g1.mp4",
                    "title": "G1",
                    "content_type": "knowledge",
                    "external_id": "K001",
                }
            ]
        },
    )
    assert created.status_code == 200
    created_video = created.json()["videos"][0]
    assert created_video["id"] == "knowledge_K001"
    assert created_video["content_type"] == "knowledge"
    assert created_video["knowledge_code"] == "K001"

    video_dir = tmp_path / "videos" / "knowledge_K001"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8"
    )

    listed = client.get("/api/videos").json()
    assert listed["videos"][0]["title"] == "G1"
    assert listed["videos"][0]["external_id"] == "K001"

    artifacts = client.get("/api/videos/knowledge_K001/artifacts").json()
    assert artifacts["subtitles"][0]["text"] == "你好"

    rerun = client.post("/api/videos/knowledge_K001/rerun", json={"phase": "transcribe"})
    assert rerun.status_code == 200
    assert not (video_dir / "subtitles.srt").exists()


def test_add_question_without_url_waits_for_url(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    created = client.post(
        "/api/videos",
        json={"items": [{"content_type": "question", "external_id": "Q001", "title": "Question 1"}]},
    )

    assert created.status_code == 200
    video = created.json()["videos"][0]
    assert video["id"] == "question_Q001"
    assert video["source_url"] == ""
    assert video["status"] == "missing_url"
    assert video["current_phase"] == "waiting_for_url"
    assert video["question_id"] == "Q001"


def test_add_video_with_empty_url_still_waits_when_cms_fetch_fails(tmp_path, monkeypatch):
    def fail_token(env, config):
        raise RuntimeError("cms unavailable")

    monkeypatch.setattr("server.app.api.get_token", fail_token)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    created = client.post(
        "/api/videos",
        json={"items": [{"content_type": "question", "external_id": "Q404", "title": "Question 404"}]},
    )

    assert created.status_code == 200
    video = created.json()["videos"][0]
    assert video["source_url"] == ""
    assert video["status"] == "missing_url"
    assert video["current_phase"] == "waiting_for_url"


def test_delete_video_removes_record_and_storage_dir(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    client.post(
        "/api/videos",
        json={
            "items": [
                {"url": "https://example.com/course/g1.mp4", "title": "G1"},
                {"url": "https://example.com/course/g2.mp4", "title": "G2"},
            ]
        },
    )
    g1_dir = tmp_path / "videos" / "g1"
    g2_dir = tmp_path / "videos" / "g2"
    (g1_dir / "subtitles.srt").write_text("x", encoding="utf-8")
    (g2_dir / "subtitles.srt").write_text("x", encoding="utf-8")

    deleted = client.delete("/api/videos/g1")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "video_id": "g1"}
    assert not g1_dir.exists()
    assert g2_dir.exists()
    assert [video["id"] for video in client.get("/api/videos").json()["videos"]] == ["g2"]
