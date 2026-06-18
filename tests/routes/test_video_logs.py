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
