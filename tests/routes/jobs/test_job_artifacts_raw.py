"""Raw artifact bytes endpoint: media-type whitelist, dual storage, errors."""

from pathlib import Path

from server.app.routes.job_artifacts import raw_media_type
from tests.helpers import publish_legacy_intake_revision, seed_workspace_agent_definitions


def _create_job(c) -> tuple[str, Path]:
    workspace_id = c.post("/api/workspaces", json={"id": "test", "name": "default"}).json()[
        "workspace"
    ]["id"]
    seed_workspace_agent_definitions(workspace_id)
    publish_legacy_intake_revision(c.app.state.job_db, workspace_id)
    created = c.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "test",
            "source_kind": "direct_ids",
            "knowledge_point_ids": ["Q003"],
        },
    ).json()
    job = created["jobs"][0]
    return job["id"], Path(job["storage_dir"])


def test_raw_endpoint_serves_image_with_media_type(client_factory):
    with client_factory(workflows_enabled=True) as c:
        job_id, storage = _create_job(c)
        (storage / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")

        response = c.get(f"/api/jobs/{job_id}/artifacts/frame.png/raw")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == b"\x89PNG\r\n\x1a\nfake-bytes"


def test_raw_endpoint_serves_unknown_extension_as_download(client_factory):
    with client_factory(workflows_enabled=True) as c:
        job_id, storage = _create_job(c)
        (storage / "report.html").write_text("<script>alert(1)</script>", encoding="utf-8")

        response = c.get(f"/api/jobs/{job_id}/artifacts/report.html/raw")

    assert response.status_code == 200
    # 白名单外（含 .html）强制 octet-stream 下载，不经浏览器渲染。
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in response.headers.get("content-disposition", "")


def test_raw_endpoint_svg_forced_to_download(client_factory):
    with client_factory(workflows_enabled=True) as c:
        job_id, storage = _create_job(c)
        (storage / "diagram.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>',
            encoding="utf-8",
        )

        response = c.get(f"/api/jobs/{job_id}/artifacts/diagram.svg/raw")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")


def test_raw_endpoint_missing_artifact_is_404(client_factory):
    with client_factory(workflows_enabled=True) as c:
        job_id, _ = _create_job(c)

        response = c.get(f"/api/jobs/{job_id}/artifacts/nope.png/raw")

    assert response.status_code == 404


def test_raw_endpoint_rejects_traversal(client_factory):
    with client_factory(workflows_enabled=True) as c:
        job_id, _ = _create_job(c)

        response = c.get(f"/api/jobs/{job_id}/artifacts/..%2Fagent_legion.sqlite/raw")

    assert response.status_code in (400, 404)


def test_raw_endpoint_unknown_job_is_404(client_factory):
    with client_factory(workflows_enabled=True) as c:
        response = c.get("/api/jobs/missing/artifacts/frame.png/raw")

    assert response.status_code == 404


def test_raw_media_type_whitelist_matrix():
    assert raw_media_type("a.png") == "image/png"
    assert raw_media_type("a.JPG") == "image/jpeg"
    assert raw_media_type("a.mp4") == "video/mp4"
    assert raw_media_type("a.pdf") == "application/pdf"
    assert raw_media_type("a.html") == "application/octet-stream"
    assert raw_media_type("a.svg") == "application/octet-stream"
    assert raw_media_type("a") == "application/octet-stream"
    assert raw_media_type("png") == "application/octet-stream"
