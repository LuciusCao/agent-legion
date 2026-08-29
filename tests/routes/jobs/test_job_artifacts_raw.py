"""Raw artifact bytes endpoint: media-type whitelist, dual storage, errors."""

from pathlib import Path

from server.app.routes.job_artifact_raw import raw_media_type
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

    # InvalidOperationError 确定性映射 400；放宽到 404 会让守卫退化也绿灯。
    assert response.status_code == 400


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


def test_raw_response_object_stream_chunks_and_closes():
    """流分支单元验证：64 KiB 分块、BackgroundTask 挂 close、白名单内联。"""
    import asyncio
    import io

    from server.app.routes.job_artifact_raw import raw_response
    from server.app.services.job_artifact_raw import RawArtifact

    class _TrackableStream:
        """组合式包装：BytesIO 的 close 不可靠被子类覆盖；记录 read 尺寸。"""

        def __init__(self, payload: bytes):
            self._buf = io.BytesIO(payload)
            self.closed_flag = False
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return self._buf.read(size)

        def close(self) -> None:
            self.closed_flag = True
            self._buf.close()

    # 150 KiB payload：按 64 KiB 分块应为 3 块（64K + 64K + 22K）。
    payload = b"y" * (64 * 1024 * 2 + 22 * 1024)
    trackable = _TrackableStream(payload)
    raw = RawArtifact(name="frame.png", stream=trackable, size_bytes=len(payload))
    response = raw_response(raw)

    chunks = asyncio.run(_collect(response.body_iterator))
    body = b"".join(chunks)
    assert body == payload
    # 分块读取请求的尺寸即 _STREAM_CHUNK_BYTES（而非 botocore 默认 1 KiB）。
    assert all(size == 64 * 1024 for size in trackable.read_sizes)
    assert len(chunks) == 3
    # 白名单媒体内联渲染 + Content-Length 透传。
    assert response.media_type == "image/png"
    assert response.headers["content-length"] == str(len(payload))
    # BackgroundTask 指向流的 close：客户端中断/正常完成都会执行。
    assert response.background is not None
    asyncio.run(response.background())
    assert trackable.closed_flag is True


def test_raw_response_stream_attachment_disposition_for_unknown_type():
    """流分支的白名单外产物也带 attachment（与本地分支一致的强制下载）。"""
    import io

    from server.app.routes.job_artifact_raw import raw_response
    from server.app.services.job_artifact_raw import RawArtifact

    stream = io.BytesIO(b"<script>x</script>")
    raw = RawArtifact(name="page.html", stream=stream, size_bytes=24)
    response = raw_response(raw)

    assert response.media_type == "application/octet-stream"
    expected = "attachment; filename=\"page.html\"; filename*=UTF-8''page.html"
    assert response.headers["content-disposition"] == expected


async def _collect(iterator):
    chunks = []
    async for chunk in iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return chunks


def test_parse_range_header_matrix():
    from server.app.http_range import parse_range_header

    assert parse_range_header("bytes=0-99", 100) == (0, 99)
    assert parse_range_header("bytes=10-", 100) == (10, 99)
    # 越界 end 裁剪到 size-1。
    assert parse_range_header("bytes=0-999", 100) == (0, 99)
    # 起点越界 / 无效形态 / 多区间 / 后缀区间 → None（全量回退）。
    assert parse_range_header("bytes=100-", 100) is None
    assert parse_range_header("bytes=abc", 100) is None
    assert parse_range_header("bytes=0-10,20-30", 100) is None
    assert parse_range_header("bytes=-10", 100) is None
    assert parse_range_header(None, 100) is None
    assert parse_range_header("bytes=0-99", None) is None


def test_raw_response_ranged_stream_is_206():
    """对象存储分支的 ranged handle：206 + Content-Range + 正确 Content-Length。"""
    import asyncio
    import io

    from server.app.routes.job_artifact_raw import raw_response
    from server.app.services.job_artifact_raw import RawArtifact

    # open_range_stream 语义：流本身只含区间字节（端到端由 service 测试覆盖）。
    stream = io.BytesIO(b"2345")
    raw = RawArtifact(
        name="clip.mp4",
        stream=stream,
        size_bytes=10,
        range_start=2,
        range_end=5,
    )
    response = raw_response(raw)

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"
    assert response.headers["accept-ranges"] == "bytes"
    body = b"".join(asyncio.run(_collect(response.body_iterator)))
    assert body == b"2345"
