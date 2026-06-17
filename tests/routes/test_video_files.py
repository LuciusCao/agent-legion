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


def test_video_file_video_not_found(client):
    response = client.get("/api/videos/nonexistent/video")
    assert response.status_code == 404
