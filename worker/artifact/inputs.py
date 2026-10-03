"""Input-artifact download for Worker execution preparation (#160 D12, #338).

Split from ``worker.bundle_io`` for the file-size budget (that module keeps
the tar-bundle extraction and re-exports these names, so existing import
paths keep working). ``sha256_file`` lives here next to its primary consumer.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from worker._retry import run_with_retry
from worker.artifact.download import download_object_artifact
from worker.host.client import Client

# 与 host_transfer 同一 retry 语义：transient 失败指数退避，上限 3 次。
_RETRY_BACKOFF_BASE_SECONDS = 1.0
_RETRY_MAX_ATTEMPTS = 3


def sha256_file(path: Path) -> str:
    """Streamed digest: artifacts can be multi-GB, never buffer them whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_input_artifacts(
    client: Client,
    manifest: dict[str, Any],
    job_dir: Path,
    download_slots: threading.Semaphore,
) -> None:
    """Download manifest ``input_artifacts`` into job_dir, verifying digests.

    Value forms (#160 D12): a ``{"url", "sha256"}`` dict downloads straight from
    object storage (presigned GET; #338 ``content_encoding: "gzip"`` gunzips
    mid-stream, sha256 always over the uncompressed bytes); the legacy
    ``"sha256:<hash>"`` string keeps the Host CAS channel.

    消费点 digest 自验闭环（EXEC-INPUT-IDENTITY-001，#876 codex P1）：
    presigned GET 指向可变 authority key——Host claim 侧的签发比对只过滤
    签发那一瞬，对象仍可能在 Worker GET 前被并行生产者覆盖。dict ref 的
    ``sha256`` 在签发时等于 dispatch 冻结 digest，本身就是冻结身份：下载
    字节 digest 不匹配时不直接失败，而是按该 digest 回落 CAS 通道（blob
    内容寻址不可变、``stage_agent_inputs`` 已按 (job,node) 持 ref 防
    GC）——任何 transport 满足同一 digest 即同一输入；CAS 也取不到或仍
    不匹配才失败，报错携带两段信息。
    """
    for name, ref in manifest.get("input_artifacts", {}).items():
        # 纵深防御：manifest 来自 Host，但落盘路径必须留在 job_dir 内
        # （同 safe_extract_tree 的 bundle 校验）。
        relative = PurePosixPath(str(name))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe input artifact name: {name!r}")
        target = job_dir / relative
        if isinstance(ref, dict):
            url = str(ref.get("url") or "")
            gunzip = str(ref.get("content_encoding") or "") == "gzip"
            with download_slots:
                # 与 CAS 分支对齐：信号量限流 + 退避重试；每次重试重新打开
                # 下载流，.part 截断重写由 download_object_artifact 的
                # temp+rename 保证。
                def _download(url: str = url, target: Path = target, gunzip: bool = gunzip) -> None:
                    download_object_artifact(url, target, gunzip=gunzip)

                run_with_retry(
                    _download,
                    retriable=(RuntimeError,),
                    base_seconds=_RETRY_BACKOFF_BASE_SECONDS,
                    max_attempts=_RETRY_MAX_ATTEMPTS,
                )
            declared = str(ref.get("sha256") or "")
            if not declared or sha256_file(target) == declared:
                continue
            try:
                _download_cas(client, declared, target, name, download_slots)
            except (RuntimeError, OSError) as exc:
                # 失败归因：两段式报错——「presigned 段拿到重写字节」与
                # 「CAS 回落段为何也没救回来」（404/GC、transient 耗尽、
                # CAS 自验失败、本地写盘）合并成一条消息上抛。
                raise RuntimeError(
                    f"artifact digest mismatch: {name}: presigned GET delivered "
                    f"rewritten bytes and the CAS fallback failed too: {exc}"
                ) from exc
            continue
        _download_cas(client, str(ref).split(":", 1)[-1], target, name, download_slots)


def _download_cas(
    client: Client,
    digest: str,
    target: Path,
    name: str,
    download_slots: threading.Semaphore,
) -> None:
    """CAS 通道下载并按 URL digest 自验（两 ref 形态与 presigned 回落共用）。

    与 dict 分支对齐：download_slots 限流，transient 退避在 client 内部
    （host_transfer 同一 retry 语义）；blob 内容寻址不可变，digest 自验
    是纵深防线（Host 侧传输损坏/CAS 实现缺陷不至于静默落盘）。
    """
    with download_slots:
        client.download(f"/api/artifacts/{digest}", target)
    if sha256_file(target) != digest:
        raise RuntimeError(f"artifact digest mismatch: {name}")
