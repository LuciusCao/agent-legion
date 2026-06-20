import json


def test_add_video_list_artifacts_and_rerun(tmp_path, client):

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
    assert created_video["storage_dir"] == str(tmp_path / "videos" / "knowledge_K001")
    assert (tmp_path / "videos" / "knowledge_K001").is_dir()

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

    rerun = client.post("/api/videos/knowledge_K001/rerun", json={"phase": "download"})
    assert rerun.status_code == 200
    assert not (video_dir / "subtitles.srt").exists()


def test_add_question_without_url_waits_for_url(tmp_path, monkeypatch, client):
    monkeypatch.setattr("server.app.services.intake.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.services.intake.lookup_question_video",
        lambda uuid, api_url, token: type(
            "Lookup",
            (),
            {"status": "missing_url", "url": "", "title": "Question 1", "source_uuid": ""},
        )(),
    )

    created = client.post(
        "/api/videos",
        json={
            "items": [{"content_type": "question", "external_id": "Q001", "title": "Question 1"}]
        },
    )

    assert created.status_code == 200
    video = created.json()["videos"][0]
    assert video["id"] == "question_Q001"
    assert video["source_url"] == ""
    assert video["status"] == "missing_url"
    assert video["current_phase"] == "waiting_for_url"
    assert video["question_id"] == "Q001"
    assert video["storage_dir"] == str(tmp_path / "videos" / "question_Q001")
    assert (tmp_path / "videos" / "question_Q001").is_dir()


def test_add_knowledge_without_url_fetches_source_v2_from_cms(tmp_path, monkeypatch, client):
    monkeypatch.setattr("server.app.services.intake.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.services.intake.lookup_knowledge_video",
        lambda code, api_url, token: type(
            "Lookup",
            (),
            {
                "status": "found",
                "url": "https://example.com/k001.mp4",
                "title": "Knowledge 1",
                "source_uuid": "",
            },
        )(),
    )

    created = client.post(
        "/api/videos",
        json={
            "items": [{"content_type": "knowledge", "external_id": "K001", "title": "Knowledge 1"}]
        },
    )

    assert created.status_code == 200
    video = created.json()["videos"][0]
    assert video["id"] == "knowledge_K001"
    assert video["source_url"] == "https://example.com/k001.mp4"
    assert video["status"] == "queued"
    assert video["current_phase"] == "download"
    assert video["storage_dir"] == str(tmp_path / "videos" / "knowledge_K001")
    assert (tmp_path / "videos" / "knowledge_K001").is_dir()


def test_add_video_with_empty_url_still_waits_when_cms_fetch_fails(tmp_path, monkeypatch, client):
    def fail_token(env, config):
        raise RuntimeError("cms unavailable")

    monkeypatch.setattr("server.app.services.intake.get_token", fail_token)

    response = client.post(
        "/api/videos",
        json={
            "items": [{"content_type": "question", "external_id": "Q404", "title": "Question 404"}]
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "fetch_failed"
    assert client.get("/api/videos").json()["videos"] == []


def test_add_knowledge_without_url_rejects_cms_not_found(tmp_path, monkeypatch, client):
    monkeypatch.setattr("server.app.services.intake.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.services.intake.lookup_knowledge_video",
        lambda code, api_url, token: type(
            "Lookup", (), {"status": "not_found", "url": "", "title": ""}
        )(),
    )

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


def test_add_question_without_url_creates_missing_url_when_cms_resource_exists(
    tmp_path, monkeypatch, client
):
    monkeypatch.setattr("server.app.services.intake.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "server.app.services.intake.lookup_question_video",
        lambda uuid, api_url, token: type(
            "Lookup",
            (),
            {"status": "missing_url", "url": "", "title": "Question 1", "source_uuid": ""},
        )(),
    )

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


def test_add_without_url_reports_fetch_failed_when_cms_errors(tmp_path, monkeypatch, client):
    def fail_lookup(code, api_url, token):
        raise RuntimeError("cms unavailable")

    monkeypatch.setattr("server.app.services.intake.get_token", lambda env, config: "token")
    monkeypatch.setattr("server.app.services.intake.lookup_knowledge_video", fail_lookup)

    response = client.post(
        "/api/videos",
        json={"items": [{"content_type": "knowledge", "external_id": "K001"}]},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "fetch_failed"
    assert client.get("/api/videos").json()["videos"] == []


def test_add_duplicate_identity_reports_duplicate(client):

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


def test_add_duplicate_direct_url_without_external_id_reports_duplicate(client):
    first = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/videos/lesson-a.mp4",
                    "title": "First Title",
                }
            ]
        },
    )
    second = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/videos/lesson-a.mp4",
                    "title": "Second Title",
                }
            ]
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["results"][0]["status"] == "duplicate"
    videos = client.get("/api/videos").json()["videos"]
    assert len(videos) == 1
    assert videos[0]["title"] == "First Title"


def test_video_detail_read_only_does_not_update_video(tmp_path, client, monkeypatch):

    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/course/v1.mp4",
                    "title": "V1",
                    "content_type": "knowledge",
                    "external_id": "V001",
                }
            ]
        },
    )
    assert created.status_code == 200
    video_id = created.json()["videos"][0]["id"]

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

    app_db = client.app.state.db
    original_update_video = app_db.update_video
    calls = []

    def instrumented_update_video(video_id_arg, **fields):
        calls.append((video_id_arg, fields))
        return original_update_video(video_id_arg, **fields)

    monkeypatch.setattr(app_db, "update_video", instrumented_update_video)

    response = client.get(f"/api/videos/{video_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["video"]["interaction_stats"] is not None
    assert not calls, f"GET /api/videos/{video_id} should not call db.update_video"


def test_list_and_detail_return_interaction_review_status(tmp_path, client, db):

    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/course/v1.mp4",
                    "title": "V1",
                    "content_type": "knowledge",
                    "external_id": "V001",
                }
            ]
        },
    )
    assert created.status_code == 200
    video_id = created.json()["videos"][0]["id"]

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
                "status": "pending_review",
                "reviews": [
                    {"item_id": "n1", "status": "published"},
                    {"item_id": "n2", "status": "rejected"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Populate DB cache so list view can read from DB without disk I/O
    db.update_video(
        video_id,
        interaction_stats_json=json.dumps(
            {
                "example_practice": {"passed": 1, "total": 1},
                "interaction_summary": {"passed": 0, "total": 1},
            }
        ),
        interaction_review_status="partial",
    )

    listed = client.get("/api/videos").json()
    assert listed["videos"][0]["interaction_review_status"] == "partial"
    assert listed["videos"][0]["interaction_stats"] == {
        "example_practice": {"passed": 1, "total": 1},
        "interaction_summary": {"passed": 0, "total": 1},
    }

    detail = client.get(f"/api/videos/{video_id}").json()
    assert detail["video"]["interaction_review_status"] == "partial"
    assert detail["video"]["interaction_stats"] == {
        "example_practice": {"passed": 1, "total": 1},
        "interaction_summary": {"passed": 0, "total": 1},
    }


def test_list_backfills_interaction_stats_when_db_cache_empty(tmp_path, client):

    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/course/v2.mp4",
                    "title": "V2",
                    "content_type": "knowledge",
                    "external_id": "V002",
                }
            ]
        },
    )
    assert created.status_code == 200
    video_id = created.json()["videos"][0]["id"]

    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                    {"id": "n3", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    # review_result.json is missing — backfill should still produce stats with passed=0

    listed = client.get("/api/videos").json()
    assert listed["videos"][0]["interaction_stats"] == {
        "example_practice": {"passed": 0, "total": 2},
        "interaction_summary": {"passed": 0, "total": 1},
    }
    assert listed["videos"][0]["interaction_review_status"] == "all_failed"

    detail = client.get(f"/api/videos/{video_id}").json()
    assert detail["video"]["interaction_stats"] == {
        "example_practice": {"passed": 0, "total": 2},
        "interaction_summary": {"passed": 0, "total": 1},
    }
    assert detail["video"]["interaction_review_status"] == "all_failed"


def test_add_video_rejects_ssrf_url(client):
    response = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "title": "SSRF",
                    "content_type": "knowledge",
                    "external_id": "SSRF001",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "invalid"
    assert "URL" in response.json()["results"][0]["message"]


def test_add_video_rejects_file_protocol(client):
    response = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "file:///etc/passwd",
                    "title": "File",
                    "content_type": "knowledge",
                    "external_id": "FILE001",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "invalid"
    assert "URL" in response.json()["results"][0]["message"]
