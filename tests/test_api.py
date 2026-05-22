
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


def test_add_question_without_url_waits_for_url(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.api.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.api.lookup_question_video",
        lambda uuid, api_url, token: type(
            "Lookup", (), {"status": "missing_url", "url": "", "title": "Question 1"}
        )(),
    )
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


def test_add_knowledge_without_url_fetches_source_v2_from_cms(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.api.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.api.lookup_knowledge_video",
        lambda code, api_url, token: type(
            "Lookup", (), {"status": "found", "url": "https://example.com/k001.mp4", "title": "Knowledge 1"}
        )(),
    )
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    created = client.post(
        "/api/videos",
        json={"items": [{"content_type": "knowledge", "external_id": "K001", "title": "Knowledge 1"}]},
    )

    assert created.status_code == 200
    video = created.json()["videos"][0]
    assert video["id"] == "knowledge_K001"
    assert video["source_url"] == "https://example.com/k001.mp4"
    assert video["status"] == "queued"
    assert video["current_phase"] == "download"


def test_add_video_with_empty_url_still_waits_when_cms_fetch_fails(tmp_path, monkeypatch):
    def fail_token(env, config):
        raise RuntimeError("cms unavailable")

    monkeypatch.setattr("server.app.api.get_token", fail_token)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/videos",
        json={"items": [{"content_type": "question", "external_id": "Q404", "title": "Question 404"}]},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "fetch_failed"
    assert client.get("/api/videos").json()["videos"] == []


def test_add_knowledge_without_url_rejects_cms_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.api.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.api.lookup_knowledge_video",
        lambda code, api_url, token: type("Lookup", (), {"status": "not_found", "url": "", "title": ""})(),
    )
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/videos",
        json={"items": [{"content_type": "knowledge", "external_id": "K404"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["status"] == "not_found"
    assert body["results"][0]["external_id"] == "K404"
    assert body["videos"] == []
    assert client.get("/api/videos").json()["videos"] == []


def test_add_question_without_url_creates_missing_url_when_cms_resource_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.api.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.api.lookup_question_video",
        lambda uuid, api_url, token: type(
            "Lookup", (), {"status": "missing_url", "url": "", "title": "Question 1"}
        )(),
    )
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/videos",
        json={"items": [{"content_type": "question", "external_id": "Q001"}]},
    )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "created_missing_url"
    assert result["video"]["id"] == "question_Q001"
    assert result["video"]["status"] == "missing_url"
    assert result["video"]["current_phase"] == "waiting_for_url"


def test_add_without_url_reports_fetch_failed_when_cms_errors(tmp_path, monkeypatch):
    def fail_lookup(code, api_url, token):
        raise RuntimeError("cms unavailable")

    monkeypatch.setattr("server.app.api.get_token", lambda env, config: "token")
    monkeypatch.setattr("server.app.api.lookup_knowledge_video", fail_lookup)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/videos",
        json={"items": [{"content_type": "knowledge", "external_id": "K001"}]},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "fetch_failed"
    assert client.get("/api/videos").json()["videos"] == []


def test_add_duplicate_identity_reports_duplicate(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    first = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k001.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                }
            ]
        },
    )
    second = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k001-new.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                }
            ]
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["results"][0]["status"] == "duplicate"
    assert len(client.get("/api/videos").json()["videos"]) == 1


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


def test_batch_delete_returns_per_video_results(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    client.post(
        "/api/videos",
        json={
            "items": [
                {"url": "https://example.com/k1.mp4", "content_type": "knowledge", "external_id": "K001"},
                {"url": "https://example.com/k2.mp4", "content_type": "knowledge", "external_id": "K002"},
            ]
        },
    )

    response = client.post(
        "/api/videos/batch/delete",
        json={"video_ids": ["knowledge_K001", "missing"]},
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {"video_id": "knowledge_K001", "status": "deleted", "message": ""},
        {"video_id": "missing", "status": "not_found", "message": "Video not found"},
    ]
    assert [v["id"] for v in client.get("/api/videos").json()["videos"]] == ["knowledge_K002"]


def test_batch_rerun_returns_per_video_results_and_normalizes_question_phase(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    client.post(
        "/api/videos",
        json={
            "items": [
                {"url": "https://example.com/q1.mp4", "content_type": "question", "external_id": "Q001"},
                {"url": "https://example.com/k1.mp4", "content_type": "knowledge", "external_id": "K001"},
            ]
        },
    )

    response = client.post(
        "/api/videos/batch/rerun",
        json={"video_ids": ["question_Q001", "knowledge_K001", "missing"], "phase": "interaction_generate"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {"video_id": "question_Q001", "status": "rerun", "phase": "assemble", "message": ""},
        {"video_id": "knowledge_K001", "status": "rerun", "phase": "interaction_generate", "message": ""},
        {"video_id": "missing", "status": "not_found", "phase": "interaction_generate", "message": "Video not found"},
    ]
    assert client.get("/api/videos/question_Q001").json()["video"]["current_phase"] == "assemble"


def test_package_selected_videos_and_download(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    client.post(
        "/api/videos",
        json={
            "items": [
                {"url": "https://example.com/k1.mp4", "content_type": "knowledge", "external_id": "K001"},
                {"url": "https://example.com/k2.mp4", "content_type": "knowledge", "external_id": "K002"},
            ]
        },
    )
    for video_id in ["knowledge_K001", "knowledge_K002"]:
        video_dir = tmp_path / "videos" / video_id
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "metadata.json").write_text(f'{{"id":"{video_id}"}}', encoding="utf-8")

    response = client.post("/api/package", json={"video_ids": ["knowledge_K002"]})

    assert response.status_code == 200
    body = response.json()
    assert body["download_url"].startswith("/api/packages/")
    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] in {"application/zip", "application/x-zip-compressed"}


def test_package_download_rejects_path_traversal(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    response = client.get("/api/packages/%2e%2e/%2e%2e/%2e%2e/etc/passwd")
    assert response.status_code == 404
