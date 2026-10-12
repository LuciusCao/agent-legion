"""Raw artifact bytes endpoint: media-type whitelist, dual storage, errors."""

from pathlib import Path

from server.app.routes.job_artifact_raw import raw_media_type
from tests.fakes.storage import FakeObjectStorage
from tests.helpers import publish_legacy_intake_revision, seed_workspace_agent_definitions
from tests.routes.jobs.external_artifact_testlib import _register_object_artifact


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
    with client_factory() as c:
        job_id, storage = _create_job(c)
        (storage / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")

        response = c.get(f"/api/jobs/{job_id}/artifacts/frame.png/raw")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == b"\x89PNG\r\n\x1a\nfake-bytes"


def test_raw_endpoint_serves_nested_artifact_name(client_factory):
    """#1178 codex 复审 P2（第 6 轮收口形态）：声明产物名可含 /
    （reports/final.mp4——Worker 解包与 promote 都保留子目录），嵌套名
    的 raw 读取走独立静态前缀路由（与文本路由零撞形——贪婪后缀形态曾
    把名为 x/raw 的产物文本 URL 按前缀名 x 吞掉）。
    字面 / 形态（前端按段编码后的 URL）与 %2F 形态（整名编码的旧客户
    端）都必须命中：ASGI 解码后两者同路径。"""
    with client_factory() as c:
        job_id, storage = _create_job(c)
        (storage / "reports").mkdir(parents=True, exist_ok=True)
        (storage / "reports" / "final.mp4").write_bytes(b"mp4-bytes")

        literal = c.get(f"/api/jobs/{job_id}/raw-artifacts/reports/final.mp4")
        encoded = c.get(f"/api/jobs/{job_id}/raw-artifacts/reports%2Ffinal.mp4")

    for response in (literal, encoded):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("video/mp4")
        assert response.content == b"mp4-bytes"


def test_raw_suffix_artifact_name_text_and_nested_raw_both_servable(client_factory):
    """#1178 codex 复审 P2（第 6 轮收口）：名为 x/raw 的既有产物不受路由
    形态影响——文本读取重新落入文本路由（贪婪后缀形态曾按前缀名 x 吞
    掉=回归）；其 raw 读取经独立前缀路由照常服务。既有产物不被重新定
    义为非法（对象存储清单是权威副本）。"""
    with client_factory() as c:
        job_id, storage = _create_job(c)
        (storage / "reports" / "output").mkdir(parents=True, exist_ok=True)
        (storage / "reports" / "output" / "raw").write_text('{"v": 1}', encoding="utf-8")

        text = c.get(f"/api/jobs/{job_id}/artifacts/reports/output/raw")
        raw = c.get(f"/api/jobs/{job_id}/raw-artifacts/reports/output/raw")

    assert text.status_code == 200
    assert text.json() == {"name": "reports/output/raw", "content": '{"v": 1}'}
    assert raw.status_code == 200
    assert raw.content == b'{"v": 1}'


def test_raw_endpoint_rejects_nested_traversal(client_factory):
    """嵌套名形态下的穿越仍被服务层白名单拒绝（is_downloadable_artifact_name
    拒 .. 段）——独立前缀路由同样不新增穿越面。"""
    with client_factory() as c:
        job_id, _ = _create_job(c)

        response = c.get(f"/api/jobs/{job_id}/raw-artifacts/reports/../../etc/passwd")

    assert response.status_code == 400


def test_raw_endpoint_prefers_manifest_object_over_stale_local(client_factory, monkeypatch):
    """#1178 codex 复审 P2（重取语义）：远程 Worker 重跑后对象存储已是新
    字节而宿主 job_dir 缓存还是旧的——raw 路由 manifest-first（权威副本
    在对象存储，EXEC-ARTIFACT-STORE-001），本地文件只在无 manifest 行的
    legacy 形态下使用；前端字节桥（cache: no-store）的重取通道不会静默
    播旧媒体。"""
    with client_factory() as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", FakeObjectStorage())
        job_id, storage = _create_job(c)
        payload = b"current-object-bytes"
        job = c.app.state.job_db.get_job(job_id)
        _register_object_artifact(c, job, "final.mp4", payload, gzipped=False)
        (storage / "final.mp4").write_bytes(b"stale-local-bytes")

        response = c.get(f"/api/jobs/{job_id}/raw-artifacts/final.mp4")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.content == payload


def test_raw_endpoint_serves_unknown_extension_as_download(client_factory):
    with client_factory() as c:
        job_id, storage = _create_job(c)
        (storage / "report.html").write_text("<script>alert(1)</script>", encoding="utf-8")

        response = c.get(f"/api/jobs/{job_id}/artifacts/report.html/raw")

    assert response.status_code == 200
    # 白名单外（含 .html）强制 octet-stream 下载，不经浏览器渲染。
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in response.headers.get("content-disposition", "")


def test_raw_endpoint_svg_forced_to_download(client_factory):
    with client_factory() as c:
        job_id, storage = _create_job(c)
        (storage / "diagram.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>',
            encoding="utf-8",
        )

        response = c.get(f"/api/jobs/{job_id}/artifacts/diagram.svg/raw")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")


def test_raw_endpoint_missing_artifact_is_404(client_factory):
    with client_factory() as c:
        job_id, _ = _create_job(c)

        response = c.get(f"/api/jobs/{job_id}/artifacts/nope.png/raw")

    assert response.status_code == 404


def test_raw_endpoint_rejects_traversal(client_factory):
    with client_factory() as c:
        job_id, _ = _create_job(c)

        response = c.get(f"/api/jobs/{job_id}/artifacts/..%2Fagent_legion.sqlite/raw")

    # InvalidOperationError 确定性映射 400；放宽到 404 会让守卫退化也绿灯。
    assert response.status_code == 400


def test_raw_endpoint_unknown_job_is_404(client_factory):
    with client_factory() as c:
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


def test_raw_response_gzip_passthrough_sets_content_encoding():
    """#338：.gz 对象透传（无 Range 请求的应答形态）——Content-Encoding:
    gzip、Content-Length 是压缩字节数、不声明 Accept-Ranges。#1259：带
    Range 的 .gz 请求不再走此形态（上游已切解压口径应答，见
    test_raw_endpoint_gz_range_request_is_206_decoded）。"""
    import asyncio
    import gzip
    import io

    from server.app.routes.job_artifact_raw import raw_response
    from server.app.services.job_artifact_raw import RawArtifact

    compressed = gzip.compress(b'{"ok": true}')
    raw = RawArtifact(
        name="result.json",
        stream=io.BytesIO(compressed),
        size_bytes=len(compressed),
        content_encoding="gzip",
    )
    response = raw_response(raw)

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-length"] == str(len(compressed))
    assert "accept-ranges" not in response.headers
    body = b"".join(asyncio.run(_collect(response.body_iterator)))
    assert body == compressed  # body 是存储字节，客户端/浏览器自行解压


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


def test_parse_range_header_strict_matrix():
    """严格变体：良构但不可满足 → UNSATISFIABLE_RANGE（416 语义）；
    畸形/多区间/后缀/缺头 → None（忽略 Range，全量应答）。"""
    from server.app.http_range import UNSATISFIABLE_RANGE, parse_range_header_strict

    assert parse_range_header_strict("bytes=0-99", 100) == (0, 99)
    assert parse_range_header_strict("bytes=10-", 100) == (10, 99)
    # 越界 end 裁剪到 size-1。
    assert parse_range_header_strict("bytes=0-999", 100) == (0, 99)
    # 起点越界 / end < start → 416 哨兵。
    assert parse_range_header_strict("bytes=100-", 100) is UNSATISFIABLE_RANGE
    assert parse_range_header_strict("bytes=100-200", 100) is UNSATISFIABLE_RANGE
    assert parse_range_header_strict("bytes=5-3", 100) is UNSATISFIABLE_RANGE
    # 畸形 / 多区间 / 后缀区间 / 缺头 / 未知长度 → None（忽略 Range）。
    assert parse_range_header_strict("bytes=abc", 100) is None
    assert parse_range_header_strict("bytes=0-10,20-30", 100) is None
    assert parse_range_header_strict("bytes=-10", 100) is None
    assert parse_range_header_strict(None, 100) is None
    assert parse_range_header_strict("bytes=0-99", None) is None


def test_raw_response_unsatisfiable_range_is_416():
    """416 分支：Content-Range */<total>，空 body，无 Content-Encoding。"""
    import asyncio

    from server.app.routes.job_artifact_raw import raw_response
    from server.app.services.job_artifact_raw import RawArtifact

    raw = RawArtifact(name="data.json", size_bytes=1000, unsatisfiable_range=True)
    response = raw_response(raw)

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */1000"
    assert "content-encoding" not in response.headers
    assert b"".join(asyncio.run(_collect(response.body_iterator))) == b""


def test_raw_response_unsliced_ranged_stream_is_sliced_206():
    """#1259：unsliced_stream（.gz 解压全量流 + 区间标记）由响应层切片：
    206 + Content-Range（解压口径），body 恰好是区间字节，无 Content-Encoding。"""
    import asyncio
    import io

    from server.app.routes.job_artifact_raw import raw_response
    from server.app.services.job_artifact_raw import RawArtifact

    payload = b"0123456789" * 100
    raw = RawArtifact(
        name="clip.mp4",
        stream=io.BytesIO(payload),
        size_bytes=len(payload),
        range_start=10,
        range_end=19,
        unsliced_stream=True,
    )
    response = raw_response(raw)

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 10-19/{len(payload)}"
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"
    assert "content-encoding" not in response.headers
    assert b"".join(asyncio.run(_collect(response.body_iterator))) == payload[10:20]


def _seed_gz_object_artifact(c, monkeypatch, job_id: str, name: str, payload: bytes) -> None:
    """manifest-first 对象分支：登记 .gz 形态的权威副本行（本地无缓存）。"""
    monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", FakeObjectStorage())
    job = c.app.state.job_db.get_job(job_id)
    _register_object_artifact(c, job, name, payload, gzipped=True)


def test_raw_endpoint_gz_range_request_is_206_decoded(client_factory, monkeypatch):
    """#1259（内置产物预览回归）：对象存储 .gz 产物 + Range → 206 +
    Content-Range（解压口径）、无 Content-Encoding——修复前回 200 + gzip
    透传，浏览器网络栈直接拒绝（媒体/文本卡片全挂）。两个 raw 入口
    （单段后缀路由与嵌套前缀路由）行为等价。"""
    with client_factory() as c:
        job_id, _ = _create_job(c)
        payload = b"0123456789" * 100
        _seed_gz_object_artifact(c, monkeypatch, job_id, "clip.mp4", payload)

        bare = c.get(f"/api/jobs/{job_id}/artifacts/clip.mp4/raw", headers={"Range": "bytes=10-19"})
        nested = c.get(
            f"/api/jobs/{job_id}/raw-artifacts/clip.mp4", headers={"Range": "bytes=10-19"}
        )

    for response in (bare, nested):
        assert response.status_code == 206
        assert response.headers["content-range"] == f"bytes 10-19/{len(payload)}"
        assert response.headers["content-length"] == "10"
        assert response.headers["accept-ranges"] == "bytes"
        assert "content-encoding" not in response.headers
        assert response.content == payload[10:20]


def test_raw_endpoint_gz_open_ended_range_covers_full_body(client_factory, monkeypatch):
    """#1259：Range: bytes=0-（媒体 preload 形态）→ 206 覆盖解压全文。"""
    with client_factory() as c:
        job_id, _ = _create_job(c)
        payload = b"0123456789" * 100
        _seed_gz_object_artifact(c, monkeypatch, job_id, "clip.mp4", payload)

        response = c.get(
            f"/api/jobs/{job_id}/raw-artifacts/clip.mp4", headers={"Range": "bytes=0-"}
        )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-{len(payload) - 1}/{len(payload)}"
    assert "content-encoding" not in response.headers
    assert response.content == payload


def test_raw_endpoint_gz_range_beyond_body_is_416(client_factory, monkeypatch):
    """#1259：起点越过解压长度 → 416 + Content-Range: */<解压长度>，
    无 Content-Encoding。"""
    with client_factory() as c:
        job_id, _ = _create_job(c)
        payload = b"0123456789" * 100
        _seed_gz_object_artifact(c, monkeypatch, job_id, "clip.mp4", payload)

        response = c.get(
            f"/api/jobs/{job_id}/raw-artifacts/clip.mp4", headers={"Range": "bytes=2000-"}
        )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(payload)}"
    assert "content-encoding" not in response.headers


def test_raw_endpoint_gz_no_range_keeps_gzip_passthrough(client_factory, monkeypatch):
    """#338 透传在无 Range 时不变（下载/字节桥链路）：Content-Encoding:
    gzip + 压缩口径 Content-Length；httpx 按 Content-Encoding 解码还原内容。"""
    import gzip

    with client_factory() as c:
        job_id, _ = _create_job(c)
        payload = b"0123456789" * 100
        _seed_gz_object_artifact(c, monkeypatch, job_id, "clip.mp4", payload)

        response = c.get(f"/api/jobs/{job_id}/artifacts/clip.mp4/raw")

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-length"] == str(len(gzip.compress(payload)))
    assert "accept-ranges" not in response.headers
    assert response.content == payload
