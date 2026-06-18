def test_delete_video_removes_record_and_storage_dir(tmp_path, client):

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


def test_batch_delete_returns_per_video_results(client):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
                {
                    "url": "https://example.com/k2.mp4",
                    "content_type": "knowledge",
                    "external_id": "K002",
                },
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


def test_batch_rerun_returns_per_video_results_and_normalizes_question_phase(client, db):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/q1.mp4",
                    "content_type": "question",
                    "external_id": "Q001",
                },
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
            ]
        },
    )
    db.update_video("question_Q001", status="completed", current_phase="assemble")
    db.update_video("knowledge_K001", status="completed", current_phase="assemble")

    response = client.post(
        "/api/videos/batch/rerun",
        json={
            "video_ids": ["question_Q001", "knowledge_K001", "missing"],
            "phase": "interaction_generate",
        },
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {"video_id": "question_Q001", "status": "rerun", "phase": "assemble", "message": ""},
        {
            "video_id": "knowledge_K001",
            "status": "rerun",
            "phase": "interaction_generate",
            "message": "",
        },
        {
            "video_id": "missing",
            "status": "not_found",
            "phase": "interaction_generate",
            "message": "Video not found",
        },
    ]
    assert client.get("/api/videos/question_Q001").json()["video"]["current_phase"] == "assemble"


def test_batch_rerun_from_failed_phase(client, db):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
                {
                    "url": "https://example.com/k2.mp4",
                    "content_type": "knowledge",
                    "external_id": "K002",
                },
                {
                    "url": "https://example.com/q1.mp4",
                    "content_type": "question",
                    "external_id": "Q001",
                },
            ]
        },
    )
    db.update_video("knowledge_K001", status="failed", current_phase="chapter_generate")
    db.update_video("knowledge_K002", status="failed", current_phase="subtitle_review")
    db.update_video("question_Q001", status="completed", current_phase="assemble")

    response = client.post(
        "/api/videos/batch/rerun",
        json={
            "video_ids": ["knowledge_K001", "knowledge_K002", "question_Q001"],
            "phase": "__failed__",
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0] == {
        "video_id": "knowledge_K001",
        "status": "rerun",
        "phase": "chapter_generate",
        "message": "",
    }
    assert results[1] == {
        "video_id": "knowledge_K002",
        "status": "rerun",
        "phase": "subtitle_review",
        "message": "",
    }
    assert results[2]["video_id"] == "question_Q001"
    assert results[2]["status"] == "skipped"
    assert "completed" in results[2]["message"]
    assert (
        client.get("/api/videos/knowledge_K001").json()["video"]["current_phase"]
        == "chapter_generate"
    )
    assert (
        client.get("/api/videos/knowledge_K002").json()["video"]["current_phase"]
        == "subtitle_review"
    )
    assert client.get("/api/videos/question_Q001").json()["video"]["current_phase"] == "assemble"


def test_single_run_to_rejects_invalid_phase(client):
    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/q1.mp4",
                    "content_type": "question",
                    "external_id": "Q001",
                }
            ]
        },
    )
    video_id = created.json()["videos"][0]["id"]

    response = client.post(
        f"/api/videos/{video_id}/run-to",
        json={"target_phase": "interaction_generate"},
    )

    assert response.status_code == 400
    assert "不适用于该视频类型" in response.json()["detail"]


def test_batch_run_to_returns_per_video_results(client):
    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
                {
                    "url": "https://example.com/q1.mp4",
                    "content_type": "question",
                    "external_id": "Q001",
                },
            ]
        },
    )
    ids = [video["id"] for video in created.json()["videos"]]

    response = client.post(
        "/api/videos/batch/run-to",
        json={"video_ids": ids, "target_phase": "interaction_generate"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["video_id"] == "knowledge_K001"
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "skipped"


def test_get_video_not_found(client):
    response = client.get("/api/videos/nonexistent")
    assert response.status_code == 404


def test_rerun_not_found(client):
    response = client.post("/api/videos/nonexistent/rerun", json={"phase": "download"})
    assert response.status_code == 404


def test_rerun_busy(client, db):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    db.start_phase("knowledge_K001", "download", ["cmd"])
    db.update_video("knowledge_K001", status="running", current_phase="download")

    response = client.post("/api/videos/knowledge_K001/rerun", json={"phase": "download"})
    assert response.status_code == 409


def test_run_to_not_found(client):
    response = client.post("/api/videos/nonexistent/run-to", json={"target_phase": "download"})
    assert response.status_code == 404


def test_run_to_busy(client, db):
    db.create_video("https://example.com/k1.mp4", content_type="knowledge", external_id="K001")
    db.start_phase("knowledge_K001", "download", ["cmd"])
    db.update_video("knowledge_K001", status="running", current_phase="download")

    response = client.post("/api/videos/knowledge_K001/run-to", json={"target_phase": "download"})
    assert response.status_code == 409


def test_delete_not_found(client):
    response = client.delete("/api/videos/nonexistent")
    assert response.status_code == 404


def test_rerun_invalid_phase(client):
    created = client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/k1.mp4", "title": "K1"}]},
    )
    video_id = created.json()["videos"][0]["id"]
    client.app.state.db.update_video(video_id, status="completed", current_phase="assemble")

    response = client.post(f"/api/videos/{video_id}/rerun", json={"phase": "invalid_phase"})
    assert response.status_code == 400


def test_run_to_success(client, monkeypatch):
    created = client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/k1.mp4", "title": "K1"}]},
    )
    video_id = created.json()["videos"][0]["id"]

    monkeypatch.setattr(
        "server.app.routes.videos.submit_run_to_phase",
        lambda db, settings, video_id, **kwargs: {
            "video_id": video_id,
            "status": "accepted",
            "phase": "download",
            "message": "",
        },
    )

    response = client.post(f"/api/videos/{video_id}/run-to", json={"target_phase": "download"})
    assert response.status_code == 200
    assert response.json()["result"]["status"] == "accepted"
