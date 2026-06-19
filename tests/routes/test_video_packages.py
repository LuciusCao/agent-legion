from pathlib import Path


def test_package_selected_videos_and_download(tmp_path, client, monkeypatch):
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

    # Execute package synchronously in test by mocking executor.submit to run immediately
    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    response = client.post("/api/package", json={"video_ids": ["knowledge_K002"]})

    assert response.status_code == 200
    assert response.json()["accepted"] is True

    # After synchronous execution, the package file should exist
    packages = list(client.app.state.settings.packages_dir.glob("*.zip"))
    assert len(packages) == 1
    download = client.get(f"/api/packages/{packages[0].name}")
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


def test_package_sets_packed_true(tmp_path, client, monkeypatch):
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

    # Execute package synchronously in test by mocking executor.submit to run immediately
    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    response = client.post("/api/package", json={"video_ids": [video_id]})

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    video = client.app.state.db.get_video(video_id)
    assert video["packed"] == 1

    # Package record should also be persisted
    packages = client.app.state.db.list_packages()
    assert len(packages) == 1
    assert packages[0]["path"].endswith(".zip")


def test_list_packages_returns_recent_packages(tmp_path, client, monkeypatch):
    video_id = "knowledge_K001"
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
    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "metadata.json").write_text('{"id":"knowledge_K001"}', encoding="utf-8")
    client.app.state.db.update_video(video_id, status="completed", current_phase="assemble")

    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    client.post("/api/package", json={"video_ids": [video_id]})

    response = client.get("/api/packages")
    assert response.status_code == 200
    data = response.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["path"].endswith(".zip")


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


def test_package_download_runtime_error(client, monkeypatch):
    def boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Path, "resolve", boom)
    response = client.get("/api/packages/test.zip")
    assert response.status_code == 404


def test_package_with_custom_name(tmp_path, client, monkeypatch):
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

    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    response = client.post("/api/package", json={"video_ids": [video_id], "name": "我的批次"})
    assert response.status_code == 200
    assert response.json()["accepted"] is True

    packages = client.app.state.db.list_packages()
    assert len(packages) == 1
    assert packages[0]["name"] == "我的批次"
    assert packages[0]["video_count"] == 1
    assert packages[0]["size_bytes"] > 0


def test_delete_package(tmp_path, client, monkeypatch):
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

    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    client.post("/api/package", json={"video_ids": [video_id]})
    pkg = client.app.state.db.list_packages(limit=1)[0]
    package_path = client.app.state.settings.data_dir / pkg["path"]
    assert package_path.exists()

    response = client.delete(f"/api/packages/{pkg['id']}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not package_path.exists()
    assert client.app.state.db.list_packages(limit=10) == []


def test_delete_package_rejects_path_outside_packages_dir(tmp_path, client):
    outside_path = tmp_path / "outside-package.zip"
    outside_path.write_bytes(b"outside")
    client.app.state.db.insert_package(str(outside_path), name="Outside Package")
    package = client.app.state.db.list_packages(limit=1)[0]

    response = client.delete(f"/api/packages/{package['id']}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Package not found"
    assert outside_path.exists()
    assert any(
        item["id"] == package["id"] for item in client.app.state.db.list_packages(limit=1000)
    )


def test_delete_package_not_found(client):
    response = client.delete("/api/packages/99999")
    assert response.status_code == 404


def test_patch_package_name(tmp_path, client, monkeypatch):
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

    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    client.post("/api/package", json={"video_ids": [video_id]})
    pkg = client.app.state.db.list_packages(limit=1)[0]

    response = client.patch(f"/api/packages/{pkg['id']}", json={"name": "新名称"})
    assert response.status_code == 200
    assert response.json()["name"] == "新名称"


def test_list_packages_returns_new_fields(tmp_path, client, monkeypatch):
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

    def _sync_submit(fn):
        fn()

    monkeypatch.setattr("server.app.routes.packages._package_executor.submit", _sync_submit)

    client.post("/api/package", json={"video_ids": [video_id], "name": "测试批次"})

    response = client.get("/api/packages")
    assert response.status_code == 200
    data = response.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["name"] == "测试批次"
    assert data["packages"][0]["video_count"] == 1
    assert data["packages"][0]["size_bytes"] > 0
