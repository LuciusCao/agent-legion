"""Worker input_artifacts 下载通道（#160 D12、#338、#876）：dict 形态
presigned GET、digest 自验、失配/失败回落 CAS。

自 tests/workers/test_artifact_object_channel.py 拆出（文件体积纪律）：
该文件留 presigned PUT 直传/队列族，本文件收 input 下载族——
EXEC-INPUT-IDENTITY-001 消费点闭环（网格 INV-1 的 C8 列）的主要钉场。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import threading
from pathlib import Path
from typing import Any, BinaryIO

import pytest
import requests

from worker.artifact import download as artifact_download
from worker.artifact import inputs as artifact_inputs
from worker.bundle_io import download_input_artifacts

pytestmark = pytest.mark.no_db

PAYLOAD = b"artifact-bytes" * 100
HASH = hashlib.sha256(PAYLOAD).hexdigest()


class _DownloadFakeClient:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs
        self.requests: list[str] = []

    def download(self, path: str, destination: Path) -> None:
        self.requests.append(path)
        if path not in self._blobs:
            raise RuntimeError(f"download failed: {path}: HTTP 404")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._blobs[path])


def _fake_open_download(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    urls: list[str] = []

    def _open(url: str) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(artifact_download, "_open_download", _open)
    return urls


def _fake_open_download_raising(monkeypatch: pytest.MonkeyPatch, error: str) -> None:
    def _open(url: str) -> io.BytesIO:
        raise RuntimeError(error)

    monkeypatch.setattr(artifact_download, "_open_download", _open)


def test_download_input_artifacts_dict_form_uses_presigned_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _fake_open_download(monkeypatch, PAYLOAD)
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert urls == ["https://s3.test/get/x?sig=1"]
    assert client.requests == []  # 旧 CAS 通道未被调用
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_dict_form_verifies_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_open_download(monkeypatch, b"tampered")
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    with pytest.raises(RuntimeError, match="digest mismatch"):
        download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]


def test_download_input_artifacts_gzip_form_gunzips_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#338：ref 带 content_encoding=gzip → 边下边解压落盘，sha256 按未压缩
    字节校验（与 content_hash 语义一致）。"""
    _fake_open_download(monkeypatch, gzip.compress(PAYLOAD))
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {
                "url": "https://s3.test/get/x?sig=1",
                "sha256": HASH,
                "content_encoding": "gzip",
            },
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_gzip_form_detects_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gzip 形态下被篡改的对象解压后 sha256 不匹配，照样拒绝。"""
    _fake_open_download(monkeypatch, gzip.compress(b"tampered"))
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {
                "url": "https://s3.test/get/x?sig=1",
                "sha256": HASH,
                "content_encoding": "gzip",
            },
        }
    }

    with pytest.raises(RuntimeError, match="digest mismatch"):
        download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]


def test_download_input_artifacts_dict_form_falls_back_to_cas_on_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#876 codex P1 消费点闭环（EXEC-INPUT-IDENTITY-001）：presigned GET
    指向可变 authority key，签发后对象被并行生产者覆盖（下载字节 digest
    不匹配 ref.sha256）——ref 的 sha256 即 dispatch 冻结身份，按它回落
    CAS 通道拿冻结字节，准备照常完成。"""
    urls = _fake_open_download(monkeypatch, b"rewritten-by-parallel-producer")
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert urls == ["https://s3.test/get/x?sig=1"]
    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_fallback_failure_carries_both_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """presigned 段拿到重写字节、CAS 段也取不到（blob GC/404）：报错必须
    能区分两段——「对象被覆盖」与「回落失败原因」都在消息里。"""
    _fake_open_download(monkeypatch, b"rewritten")
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    with pytest.raises(RuntimeError) as excinfo:
        download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "presigned GET delivered rewritten bytes" in message
    assert "CAS fallback failed" in message
    assert "HTTP 404" in message


def test_download_input_artifacts_gzip_form_falls_back_to_cas_on_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gzip 形态同族：.gz 对象被覆盖（解压后 digest 不匹配）同样回落 CAS
    拿未压缩冻结字节——digest 口径一致（未压缩 sha256）。"""
    _fake_open_download(monkeypatch, gzip.compress(b"tampered"))
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {
                "url": "https://s3.test/get/x?sig=1",
                "sha256": HASH,
                "content_encoding": "gzip",
            },
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_fallback_cas_bytes_are_digest_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回落段的纵深防线：CAS 通道取回的字节同样按 URL digest 自验——Host
    侧传输损坏/假 blob 不会静默落盘，报错仍携带两段信息。"""
    _fake_open_download(monkeypatch, b"rewritten")
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": b"corrupt-cas-bytes"})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    with pytest.raises(RuntimeError) as excinfo:
        download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "presigned GET delivered rewritten bytes" in message
    assert "artifact digest mismatch" in message


def test_download_input_artifacts_presigned_http_failure_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2（#876 B 员 P2-）：presigned HTTP 失败（403/404/5xx，重试耗尽）
    同样回落 CAS——ref.sha256 即冻结身份，传输失败不等于身份未知。"""
    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    _fake_open_download_raising(monkeypatch, "artifact download failed with HTTP 403")
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_truncated_gzip_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2：解码失败（截断 gzip 流 → EOFError，非 OSError 族）不得绕过两
    段式——同样归拢进 CAS 回落。"""
    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    _fake_open_download(monkeypatch, gzip.compress(PAYLOAD)[:5])  # 截断的 gzip 流
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {
                "url": "https://s3.test/get/x?sig=1",
                "sha256": HASH,
                "content_encoding": "gzip",
            },
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_presigned_failure_and_cas_missing_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2：两段皆败的报错归因——presigned 段的失败原因（HTTP 403）与
    CAS 段的失败原因（HTTP 404）都要在消息里。"""
    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    _fake_open_download_raising(monkeypatch, "artifact download failed with HTTP 403")
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    with pytest.raises(RuntimeError) as excinfo:
        download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "presigned GET failed" in message
    assert "HTTP 403" in message
    assert "CAS fallback failed" in message
    assert "HTTP 404" in message


def test_download_input_artifacts_string_form_keeps_cas_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _fake_open_download(monkeypatch, b"unused")
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {"input_artifacts": {"inputs/q.json": f"sha256:{HASH}"}}

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert urls == []
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_open_download_error_hides_presigned_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下载侧同样：requests 异常的 str(exc) 含签名 URL，包装后只留类型名。"""

    def _get(url: str, **kwargs: Any) -> Any:
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='s3.test', port=443): Max retries exceeded"
            " with url: /get/x?X-Amz-Credential=AKID&X-Amz-Signature=abc123"
        )

    monkeypatch.setattr(artifact_download.requests, "get", _get)
    with pytest.raises(RuntimeError) as excinfo:
        artifact_download.download_object_artifact(
            "https://s3.test/get/x?X-Amz-Signature=abc123", tmp_path / "job" / "q.json"
        )
    message = str(excinfo.value)
    assert "ConnectionError" in message
    assert "X-Amz-Signature" not in message
    assert "X-Amz-Credential" not in message
    assert "s3.test" not in message


def test_download_input_artifacts_dict_form_retries_with_semaphore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dict 分支与 CAS 分支对齐：download_slots 限流 + transient 退避重试。"""
    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    slots = threading.Semaphore(1)
    calls: list[str] = []

    def _open(url: str) -> io.BytesIO:
        assert not slots.acquire(blocking=False)  # 信号量必须已持有
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("artifact download failed: ConnectionError")
        return io.BytesIO(PAYLOAD)

    monkeypatch.setattr(artifact_download, "_open_download", _open)
    client = _DownloadFakeClient({})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", slots)  # type: ignore[arg-type]

    assert len(calls) == 3  # 每次重试重新打开下载流
    assert client.requests == []
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


# --- #876 codex P2: gzip 解码全错误面归一化（下载层单点） ---


def _corrupt_deflate_body(payload: bytes) -> bytes:
    """合法 gzip 头 + 结构性非法的 deflate 体（BFINAL=1/BTYPE=0b11
    reserved）+ 原 trailer——确定性抛 zlib.error（非 OSError/EOFError）。"""
    good = gzip.compress(payload)
    return good[:10] + b"\x07" + good[-8:]


def test_gzip_decode_surface_normalizes_to_runtime_error() -> None:
    """归一化单点（copy_stream）：gzip 解码三层错误面——BadGzipFile（头/
    容器，OSError 族）、EOFError（截断）、zlib.error（deflate 体损坏）—
    —统一转 RuntimeError（'gzip decode failed'），下载层永不泄漏
    zlib.error；gunzip=False 的原流读错误原样穿透不归一。"""
    import zlib

    from worker.artifact.gzip import copy_stream

    good = gzip.compress(PAYLOAD)
    cases = {
        "BadGzipFile": b"not-a-gzip-stream",  # 头坏
        "EOFError": good[:5],  # 截断
        "error": _corrupt_deflate_body(PAYLOAD),  # deflate 体坏（zlib.error）
    }
    for expected_cause, blob in cases.items():
        with pytest.raises(RuntimeError, match="gzip decode failed") as excinfo:
            copy_stream(io.BytesIO(blob), io.BytesIO(), gunzip=True)
        assert type(excinfo.value.__cause__).__name__ == expected_cause
        assert not isinstance(excinfo.value, zlib.error)

    # 非 gunzip 形态：原流读错误不归一、原样穿透。
    class _Boom(io.RawIOBase):
        def read(self, size: int = -1) -> bytes:
            raise OSError(5, "I/O error")

    with pytest.raises(OSError):
        copy_stream(_Boom(), io.BytesIO())  # type: ignore[arg-type]


def test_download_input_artifacts_corrupt_gzip_header_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三层错误面·头坏（BadGzipFile）：归一化后照样回落 CAS。"""
    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    _fake_open_download(monkeypatch, b"not-a-gzip-stream")
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {
                "url": "https://s3.test/get/x?sig=1",
                "sha256": HASH,
                "content_encoding": "gzip",
            },
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_corrupt_deflate_body_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#876 codex P2 主回归·三层错误面·deflate 体坏（zlib.error，直接继承
    Exception）：归一化前绕过 CAS 回落裸奔硬失败；归一化后回落 CAS 拿到
    冻结字节。"""
    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    _fake_open_download(monkeypatch, _corrupt_deflate_body(PAYLOAD))
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {
                "url": "https://s3.test/get/x?sig=1",
                "sha256": HASH,
                "content_encoding": "gzip",
            },
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


# --- #876 C2-1: urllib3 读时错误族归一化（公共基类 HTTPError） ---


class _ReadErrorStream(io.RawIOBase):
    """读完头部字节后抛指定读时错误的假流——urllib3 读时错误注入 seam
    （_open_download seam 只能注字节流形态；读时错误必须在流上注入）。"""

    def __init__(self, head: bytes, error: BaseException) -> None:
        self._head = head
        self._error = error

    def read(self, size: int = -1) -> bytes:
        if self._head:
            head, self._head = self._head, b""
            return head
        raise self._error


def test_download_read_errors_normalize_to_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2-1 归一化单测：urllib3 读时错误族（公共基类 HTTPError 捕获）统一
    转 RuntimeError（'download read failed: <类型名>'），__cause__ 保留。"""
    import urllib3.exceptions

    errors: list[BaseException] = [
        urllib3.exceptions.ProtocolError(
            "Connection broken: ConnectionResetError(104)", ConnectionResetError(104, "reset")
        ),
        urllib3.exceptions.ReadTimeoutError(None, "https://s3.test/get/x", "Read timed out."),
    ]
    for error in errors:
        monkeypatch.setattr(
            artifact_download,
            "_open_download",
            lambda url, error=error: _ReadErrorStream(b"partial", error),
        )
        with pytest.raises(RuntimeError, match=f"download read failed: {type(error).__name__}") as (
            excinfo
        ):
            artifact_download.download_object_artifact(
                "https://s3.test/get/x?sig=1", tmp_path / "job" / "q.json"
            )
        assert excinfo.value.__cause__ is error


def test_download_input_artifacts_mid_stream_protocol_error_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2-1 主回归：urllib3 读时错误族（ProtocolError，中段 RST）经下载层
    归一化进 retriable——重试发生（3 次）、耗尽后回落 CAS 拿冻结字节
    （归一化前零重试零回落硬失败）。"""
    import urllib3.exceptions

    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    urls: list[str] = []

    def _open(url: str) -> BinaryIO:
        urls.append(url)
        return _ReadErrorStream(
            b"partial",
            urllib3.exceptions.ProtocolError(
                "Connection broken", ConnectionResetError(104, "reset")
            ),
        )

    monkeypatch.setattr(artifact_download, "_open_download", _open)
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert len(urls) == 3  # 归一化进 retriable：重试发生且耗尽
    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD


def test_download_input_artifacts_read_timeout_falls_back_to_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2-1 同族：ReadTimeoutError（SocketTimeout 经 _error_catcher 包装）
    同样归一化→重试→回落。"""
    import urllib3.exceptions

    monkeypatch.setattr(artifact_inputs, "_RETRY_BACKOFF_BASE_SECONDS", 0.01)

    def _open(url: str) -> BinaryIO:
        return _ReadErrorStream(
            b"", urllib3.exceptions.ReadTimeoutError(None, url, "Read timed out.")
        )

    monkeypatch.setattr(artifact_download, "_open_download", _open)
    client = _DownloadFakeClient({f"/api/artifacts/{HASH}": PAYLOAD})
    manifest = {
        "input_artifacts": {
            "inputs/q.json": {"url": "https://s3.test/get/x?sig=1", "sha256": HASH},
        }
    }

    download_input_artifacts(client, manifest, tmp_path / "job", threading.Semaphore(1))  # type: ignore[arg-type]

    assert client.requests == [f"/api/artifacts/{HASH}"]
    assert (tmp_path / "job" / "inputs" / "q.json").read_bytes() == PAYLOAD
