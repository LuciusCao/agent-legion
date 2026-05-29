import asyncio
import json
import subprocess
from pathlib import Path


def test_core_api_routes_declare_response_models(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert paths["/api/health"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/HealthResponse"}
    assert paths["/api/agents"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/AgentsResponse"}
    assert paths["/api/videos/batch/delete"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/BatchDeleteResponse"}
    assert paths["/api/videos/batch/rerun"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/BatchRerunResponse"}
    assert paths["/api/package"]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PackageResponse"}
    assert paths["/api/worker/status"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/WorkerStatusResponse"}


def test_run_to_routes_declare_response_models(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert paths["/api/videos/{video_id}/run-to"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/RunToSingleResponse"}
    assert paths["/api/videos/batch/run-to"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/BatchRunToResponse"}


def test_worker_pause_resume_api(client):
    status = client.get("/api/worker/status")
    assert status.status_code == 200
    assert status.json() == {"paused": True}
    assert client.app.state.worker_control.is_paused() is True

    paused = client.post("/api/worker/pause")
    assert paused.status_code == 200
    assert paused.json() == {"paused": True}
    assert client.app.state.worker_control.is_paused() is True

    resumed = client.post("/api/worker/resume")
    assert resumed.status_code == 200
    assert resumed.json() == {"paused": False}
    assert client.app.state.worker_control.is_paused() is False


def test_worker_tick_returns_accepted(client):
    response = client.post("/api/worker/tick")
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert client.app.state.worker_control.consume_tick() is True


def test_agents_websocket_sends_initial_list(client, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps([{"id": "main", "identityName": "Main"}]),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    agent_manager = client.app.state.agent_manager
    agent_manager.discover()

    with client.websocket_connect("/api/agents") as ws:
        data = ws.receive_json()
        assert len(data) == 1
        assert data[0]["id"] == "main"
        assert data[0]["name"] == "Main"
        assert data[0]["busy"] is False


def test_agents_websocket_broadcasts_busy_idle_updates(client, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps([{"id": "main"}]),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    agent_manager = client.app.state.agent_manager
    agent_manager.discover()

    with client.websocket_connect("/api/agents") as ws:
        ws.receive_json()
        agent_manager.set_busy(
            "main",
            {
                "id": "v1",
                "title": "T1",
                "content_type": "knowledge",
                "external_id": "K001",
                "current_phase": "download",
            },
        )
        data = ws.receive_json()
        assert data[0]["busy"] is True
        assert data[0]["current_video_id"] == "v1"
        assert data[0]["current_title"] == "T1"

        agent_manager.set_idle("main")
        data = ws.receive_json()
        assert data[0]["busy"] is False
        assert data[0]["current_video_id"] is None


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


def test_artifacts_endpoint_includes_checklist_and_review(tmp_path, client):
    import json

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
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
    )
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}), encoding="utf-8")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    (video_dir / "checklist.json").write_text(
        json.dumps({"video_id": video_id, "checklist": {"content_usability": {"issues": []}}}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"score": 95, "status": "published"}), encoding="utf-8"
    )

    artifacts = client.get(f"/api/videos/{video_id}/artifacts").json()
    assert artifacts["checklist"] is not None
    assert artifacts["checklist"]["checklist"]["content_usability"]["issues"] == []
    assert artifacts["review"] is not None
    assert artifacts["review"]["score"] == 95


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


def test_package_selected_videos_and_download(tmp_path, client):
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
    for video_id in ["knowledge_K001", "knowledge_K002"]:
        video_dir = tmp_path / "videos" / video_id
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "metadata.json").write_text(f'{{"id":"{video_id}"}}', encoding="utf-8")
        client.app.state.db.update_video(video_id, status="completed", current_phase="assemble")

    response = client.post("/api/package", json={"video_ids": ["knowledge_K002"]})

    assert response.status_code == 200
    body = response.json()
    assert body["download_url"].startswith("/api/packages/")
    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] in {"application/zip", "application/x-zip-compressed"}


def test_package_selected_unfinished_video_returns_400(client):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
            ]
        },
    )

    response = client.post("/api/package", json={"video_ids": ["knowledge_K001"]})

    assert response.status_code == 400
    assert response.json()["detail"] == "No completed videos selected for packaging"


def test_package_selected_missing_video_returns_404(client):

    response = client.post("/api/package", json={"video_ids": ["missing"]})

    assert response.status_code == 404
    assert response.json()["detail"] == "Videos not found: missing"


def test_package_with_empty_selection_returns_400(client):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
            ]
        },
    )

    response = client.post("/api/package", json={"video_ids": []})

    assert response.status_code == 400
    assert response.json()["detail"] == "No videos selected for packaging"


def test_package_without_completed_videos_returns_400(client):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
            ]
        },
    )

    response = client.post("/api/package")

    assert response.status_code == 400
    assert response.json()["detail"] == "No completed videos available for packaging"


def test_package_sets_packed_true(tmp_path, client):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
            ]
        },
    )
    video_id = "knowledge_K001"
    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "metadata.json").write_text('{"id":"knowledge_K001"}', encoding="utf-8")
    client.app.state.db.update_video(video_id, status="completed", current_phase="assemble")

    response = client.post("/api/package", json={"video_ids": [video_id]})

    assert response.status_code == 200
    video = client.app.state.db.get_video(video_id)
    assert video["packed"] == 1


def test_rerun_clears_packed(tmp_path, client):
    client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/k1.mp4",
                    "content_type": "knowledge",
                    "external_id": "K001",
                },
            ]
        },
    )
    video_id = "knowledge_K001"
    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "metadata.json").write_text('{"id":"knowledge_K001"}', encoding="utf-8")
    client.app.state.db.update_video(
        video_id, status="completed", current_phase="assemble", packed=1
    )

    response = client.post(f"/api/videos/{video_id}/rerun", json={"phase": "assemble"})

    assert response.status_code == 200
    video = client.app.state.db.get_video(video_id)
    assert video["packed"] == 0


def test_package_download_rejects_path_traversal(client):
    response = client.get("/api/packages/%2e%2e/%2e%2e/%2e%2e/etc/passwd")
    assert response.status_code == 404


def test_package_download_rejects_empty_and_directory(client):
    # Empty filename should 404
    response = client.get("/api/packages/")
    assert response.status_code == 404

    # Leading slash should 404
    response = client.get("/api/packages/%2fetc%2fpasswd")
    assert response.status_code == 404

    # Directory traversal via dot-dot should 404
    response = client.get("/api/packages/foo/bar/../baz")
    assert response.status_code == 404


def test_package_download_rejects_backslash_traversal(client):
    response = client.get("/api/packages/foo\\..\\..\\etc\\passwd")
    assert response.status_code == 404


def test_package_download_rejects_subdirectory(client, settings):
    nested = settings.packages_dir / "foo" / "bar.zip"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("fake", encoding="utf-8")
    response = client.get("/api/packages/foo/bar.zip")
    assert response.status_code == 404


# SSE tests


def test_video_event_manager_broadcast():
    import asyncio
    import json

    from server.app.events import VideoEventManager

    async def _test():
        manager = VideoEventManager()
        manager._loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        manager._clients.add(queue)

        manager.broadcast({"id": "v1", "status": "running"})
        await asyncio.sleep(0)  # yield so call_soon callback runs
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(payload)
        assert data["type"] == "video_updated"
        assert data["video"]["id"] == "v1"

        manager.broadcast_delete("v1")
        await asyncio.sleep(0)
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(payload)
        assert data["type"] == "video_deleted"
        assert data["video_id"] == "v1"

    asyncio.run(_test())


def test_video_file_returns_local_mp4_when_exists(client):
    created = client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/v1.mp4", "title": "V1"}]},
    )
    video_id = created.json()["videos"][0]["id"]
    video_dir = client.app.state.settings.videos_dir / video_id
    (video_dir / f"{video_id}.mp4").write_bytes(b"fake mp4")

    response = client.get(f"/api/videos/{video_id}/video")
    assert response.status_code == 200
    assert response.content == b"fake mp4"
    assert response.headers["content-type"] == "video/mp4"


def test_video_file_redirects_to_source_url_when_local_missing(tmp_path, client):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/v1.mp4", "title": "V1"}]},
    )
    # Do not create local mp4

    response = client.get("/api/videos/v1/video", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/v1.mp4"


def test_video_file_returns_404_when_local_and_source_url_missing(client, db, settings):
    db.create_video("", "V1")
    db.update_video("video", source_url="", storage_dir=str(settings.videos_dir / "video"))
    response = client.get("/api/videos/video/video")
    assert response.status_code == 404
    assert response.text == "Video not downloaded yet"


def test_video_event_manager_max_clients():
    import asyncio
    from unittest.mock import MagicMock

    from server.app.events import VideoEventManager

    async def _test():
        manager = VideoEventManager()
        manager._loop = asyncio.get_running_loop()

        for _ in range(VideoEventManager.MAX_CLIENTS + 5):
            await manager.connect(MagicMock())

        assert len(manager._clients) == VideoEventManager.MAX_CLIENTS

    asyncio.run(_test())


def test_video_event_manager_queue_full_cleanup():
    import asyncio

    from server.app.events import VideoEventManager

    async def _test():
        manager = VideoEventManager()
        manager._loop = asyncio.get_running_loop()
        full_queue = asyncio.Queue(maxsize=1)
        full_queue.put_nowait("block")
        manager._clients.add(full_queue)

        normal_queue = asyncio.Queue()
        manager._clients.add(normal_queue)

        manager.broadcast({"id": "v1", "status": "running"})
        await asyncio.sleep(0)

        # Full queue should have been evicted
        assert full_queue not in manager._clients
        # Normal queue should have received the message
        payload = await asyncio.wait_for(normal_queue.get(), timeout=1.0)
        assert "v1" in payload

    asyncio.run(_test())


def test_database_broadcast_on_create_and_delete(client):
    import json

    async def _test():
        event_manager = client.app.state.video_event_manager
        event_manager._loop = asyncio.get_running_loop()

        queue = asyncio.Queue()
        event_manager._clients.add(queue)

        # Create video triggers broadcast (create_video + update_video may both fire)
        created = client.post(
            "/api/videos",
            json={
                "items": [
                    {
                        "url": "https://example.com/sse_test.mp4",
                        "title": "SSE Test",
                        "content_type": "knowledge",
                        "external_id": "SSE001",
                    }
                ]
            },
        )
        assert created.status_code == 200
        video_id = created.json()["videos"][0]["id"]

        await asyncio.sleep(0)
        # Drain creation events
        while not queue.empty():
            await queue.get()

        # Delete video triggers broadcast_delete
        deleted = client.delete(f"/api/videos/{video_id}")
        assert deleted.status_code == 200

        await asyncio.sleep(0)
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(payload)
        assert data["type"] == "video_deleted"
        assert data["video_id"] == video_id

    asyncio.run(_test())


def test_list_and_detail_return_interaction_review_status(tmp_path, client, db):
    import json

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


def test_logs_empty(client):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/k1.mp4", "title": "K1"}]},
    )
    response = client.get("/api/videos/k1/logs")
    assert response.status_code == 200
    assert response.json()["log"] == ""


def test_logs_from_file(tmp_path, client, db):
    db.create_video("https://example.com/k1.mp4", "k1")
    log_path = tmp_path / "logs" / "k1-download.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("download started\n" * 1000, encoding="utf-8")
    db.start_phase("k1", "download", ["cmd"], str(log_path))

    response = client.get("/api/videos/k1/logs")
    assert response.status_code == 200
    assert "download started" in response.json()["log"]


def test_logs_tail_seek_large_file(tmp_path, client, db):
    db.create_video("https://example.com/k1.mp4", "k1")
    log_path = tmp_path / "logs" / "k1-download.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Write ~200KB log; only last 8000 chars should be returned
    prefix = "A" * 1000 + "\n"
    suffix = "Z" * 1000 + "\n"
    log_path.write_text(prefix * 100 + suffix * 100, encoding="utf-8")
    db.start_phase("k1", "download", ["cmd"], str(log_path))

    response = client.get("/api/videos/k1/logs")
    assert response.status_code == 200
    log = response.json()["log"]
    assert "Z" in log
    assert "A" not in log


def test_video_file_redirects_when_local_missing(client):
    created = client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/k1.mp4", "title": "K1"}]},
    )
    video_id = created.json()["videos"][0]["id"]
    response = client.get(f"/api/videos/{video_id}/video", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/k1.mp4"


def test_video_file_no_source_url(client, db, settings):
    db.create_video("", "V1")
    db.update_video("video", source_url="", storage_dir=str(settings.videos_dir / "video"))
    response = client.get("/api/videos/video/video")
    assert response.status_code == 404
    assert response.text == "Video not downloaded yet"


def test_rerun_invalid_phase(client):
    created = client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/k1.mp4", "title": "K1"}]},
    )
    video_id = created.json()["videos"][0]["id"]
    client.app.state.db.update_video(video_id, status="completed", current_phase="assemble")

    response = client.post(f"/api/videos/{video_id}/rerun", json={"phase": "invalid_phase"})
    assert response.status_code == 400


def test_video_file_video_not_found(client):
    response = client.get("/api/videos/nonexistent/video")
    assert response.status_code == 404


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


def test_package_download_runtime_error(client, monkeypatch):
    def boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Path, "resolve", boom)
    response = client.get("/api/packages/test.zip")
    assert response.status_code == 404


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


def test_logs_filters_sensitive_paths(tmp_path, client, db, settings):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/v1.mp4", "title": "V1"}]},
    )
    video_id = "v1"
    video_dir = settings.videos_dir / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    log_file = video_dir / "phase.log"
    log_file.write_text(
        "Downloading from https://example.com/secret.mp4\n"
        "Saved to /Users/admin/.ssh/id_rsa\n"
        "Success\n",
        encoding="utf-8",
    )
    db.start_phase(video_id, "download", ["cmd"], str(log_file))

    response = client.get(f"/api/videos/{video_id}/logs")
    assert response.status_code == 200
    log = response.json()["log"]
    assert "secret.mp4" not in log
    assert "/Users/admin/.ssh/id_rsa" not in log
    assert "Success" in log
