"""Worker-side presigned-GET download for input artifacts (#160 D12).

Split out of ``worker.bundle_io`` for the file-size budget: that module owns
the legacy Host CAS channel, this one owns the object-storage channel. The
URL comes from the authenticated claim channel, so no SSRF guard applies
(same rule as ``worker.material_fetch``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, cast

import requests
import urllib3.exceptions

from worker.artifact.gzip import copy_stream

# Single socket timeout for presigned GET downloads; aligned with the
# transfer-timeout default of the bundle/artifact channel.
_DOWNLOAD_TIMEOUT_SECONDS = 120


def sha256_file(path: Path) -> str:
    """Streamed digest: artifacts can be multi-GB, never buffer them whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_transfer_error(exc: BaseException) -> str:
    """``str()`` of a requests/network exception embeds the full presigned URL
    (signature included); keep only the type name for persisted error
    messages. Shared by the artifact upload/download channels (it lives here
    because ``artifact_upload`` importing it would close an import cycle via
    ``bundle_io``)."""
    return type(exc).__name__


def _open_download(url: str) -> BinaryIO:
    """Open a streaming reader for a presigned GET URL.

    Module-level seam: tests monkeypatch this instead of touching the
    network.
    """
    try:
        response = requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # str(exc) 含完整签名 URL；只保留类型名，防止经 error_message 落库泄漏。
        raise RuntimeError(f"artifact download failed: {describe_transfer_error(exc)}") from exc
    if response.status_code != 200:
        response.close()
        raise RuntimeError(f"artifact download failed with HTTP {response.status_code}")
    return cast(BinaryIO, response.raw)


def download_object_artifact(url: str, target: Path, *, gunzip: bool = False) -> None:
    """Stream a presigned GET to an atomic temp+rename (same .part hygiene as
    the Host-channel download); ``gunzip`` (#338) decodes mid-stream.

    读时错误面归一化（#876 C2-1，枚举纪律）：``response.raw`` 是裸
    urllib3 ``HTTPResponse``，requests 不拦截 raw read——urllib3 2.7.0
    的 ``_error_catcher``（response.py）把读时错误包装为：
    SocketTimeout → ``ReadTimeoutError``、BaseSSLError → ``SSLError``、
    IncompleteRead → ``ProtocolError``、(HTTPException, OSError) →
    ``ProtocolError``，另有 ``DecodeError``（content 解码）直属——全部
    派生自公共基类 ``urllib3.exceptions.HTTPError``。捕基类而非逐类
    型：``_error_catcher`` 的库契约就是「低层异常不漏出高层 API」，版
    本升级新增错误类型都派生自 HTTPError，基类捕获对升级稳健（本机
    实证 2.7.0）。归一化为 RuntimeError 后进入 run_with_retry 的
    retriable（中段断连是瞬时语义，重试本就该发生），耗尽后由
    inputs.py 回落 CAS。消息只带类型名——理由同 describe_transfer_error
    （str(exc) 可能嵌签名 URL，这里拿不到 url 但保持同口径）。
    """
    if not url:
        raise RuntimeError("input artifact is missing its download URL")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        try:
            with _open_download(url) as stream, temporary.open("wb") as handle:
                copy_stream(stream, handle, gunzip=gunzip)
        except urllib3.exceptions.HTTPError as exc:
            raise RuntimeError(f"download read failed: {type(exc).__name__}") from exc
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
