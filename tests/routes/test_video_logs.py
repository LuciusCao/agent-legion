from server.app.storage_paths import make_data_relative


def test_logs_empty(client):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/k1.mp4", "title": "K1"}]},
    )
    response = client.get("/api/videos/k1/logs")
    assert response.status_code == 200
    assert response.json()["log"] == ""


def test_logs_from_file(tmp_path, client, db, settings):
    db.create_video("https://example.com/k1.mp4", "k1")
    log_path = tmp_path / "logs" / "k1-download.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("download started\n" * 1000, encoding="utf-8")
    db.start_phase("k1", "download", ["cmd"], make_data_relative(log_path, settings.data_dir))

    response = client.get("/api/videos/k1/logs")
    assert response.status_code == 200
    assert "download started" in response.json()["log"]


def test_logs_tail_seek_large_file(tmp_path, client, db, settings):
    db.create_video("https://example.com/k1.mp4", "k1")
    log_path = tmp_path / "logs" / "k1-download.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Write ~200KB log; only last 8000 chars should be returned
    prefix = "A" * 1000 + "\n"
    suffix = "Z" * 1000 + "\n"
    log_path.write_text(prefix * 100 + suffix * 100, encoding="utf-8")
    db.start_phase("k1", "download", ["cmd"], make_data_relative(log_path, settings.data_dir))

    response = client.get("/api/videos/k1/logs")
    assert response.status_code == 200
    log = response.json()["log"]
    assert "Z" in log
    assert "A" not in log


def test_logs_filters_sensitive_paths(client, db, settings):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/v1.mp4", "title": "V1"}]},
    )
    video_id = "v1"
    log_file = settings.logs_dir / "v1-phase.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        "Downloading from https://example.com/secret.mp4\n"
        "Saved to /Users/admin/.ssh/id_rsa\n"
        "Success\n",
        encoding="utf-8",
    )
    db.start_phase(video_id, "download", ["cmd"], make_data_relative(log_file, settings.data_dir))

    response = client.get(f"/api/videos/{video_id}/logs")
    assert response.status_code == 200
    log = response.json()["log"]
    assert "secret.mp4" not in log
    assert "/Users/admin/.ssh/id_rsa" not in log
    assert "Success" in log


def test_logs_rejects_escape_path(tmp_path, client, db, settings):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/e1.mp4", "title": "E1"}]},
    )
    escape_file = tmp_path.parent / "escape.log"
    escape_file.write_text("escaped content", encoding="utf-8")
    db.start_phase("e1", "download", ["cmd"], "../escape.log")

    response = client.get("/api/videos/e1/logs")
    assert response.status_code == 200
    assert response.json()["log"] == ""


def test_logs_rejects_wrong_category_path(client, db, settings):
    client.post(
        "/api/videos",
        json={"items": [{"url": "https://example.com/w1.mp4", "title": "W1"}]},
    )
    wrong_file = settings.jobs_dir / "other.log"
    wrong_file.parent.mkdir(parents=True, exist_ok=True)
    wrong_file.write_text("wrong category content", encoding="utf-8")
    db.start_phase("w1", "download", ["cmd"], "jobs/other.log")

    response = client.get("/api/videos/w1/logs")
    assert response.status_code == 200
    assert response.json()["log"] == ""
