def test_legacy_videos_business_route_is_gone(client) -> None:
    response = client.get("/api/videos")
    assert response.status_code == 404


def test_legacy_video_artifacts_route_is_gone(client) -> None:
    response = client.get("/api/videos/legacy-video/artifacts")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


def test_legacy_video_hive_config_route_is_gone(client) -> None:
    response = client.get("/api/video-hive/config")
    assert response.status_code == 404


def test_legacy_video_package_route_is_gone(client) -> None:
    response = client.post("/api/package", json={"video_ids": []})
    assert response.status_code == 404
