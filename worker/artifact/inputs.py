"""Input-artifact download for Worker execution preparation (#160 D12, #338).

Split from ``worker.bundle_io`` for the file-size budget (that module keeps
the tar-bundle extraction and re-exports these names, so existing import
paths keep working). ``sha256_file`` lives in ``worker.artifact.download``
(the shared digest/transfer utility) and is re-exported here for bundle_io.
"""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath
from typing import Any

from worker._retry import run_with_retry
from worker.artifact.download import download_object_artifact, sha256_file
from worker.host.client import Client

__all__ = ["download_input_artifacts", "sha256_file"]

# 与 host_transfer 同一 retry 语义：transient 失败指数退避，上限 3 次。
_RETRY_BACKOFF_BASE_SECONDS = 1.0
_RETRY_MAX_ATTEMPTS = 3


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
    ``sha256`` 在签发时等于 dispatch 冻结 digest，本身就是冻结身份：
    presigned 段任何失败——下载成功但 digest 失配、HTTP 失败重试耗尽
    （403/404/5xx）、解码失败（gzip 三层错误面经下载层
    ``copy_stream`` 归一化为 RuntimeError）、urllib3 读时错误（
    ``download_object_artifact`` 归一化）——都按该 digest 回落 CAS 通
    道（blob 内容寻址不可变、``stage_agent_inputs`` 已按 (job,node) 持
    ref 防 GC）——任何 transport 满足同一 digest 即同一输入；两段皆
    败才失败，报错携带两段各自的原因。
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
            declared = str(ref.get("sha256") or "")
            presigned_error: BaseException | None = None
            try:
                with download_slots:
                    # 与 CAS 分支对齐：信号量限流 + 退避重试；每次重试重新
                    # 打开下载流，.part 截断重写由 download_object_artifact
                    # 的 temp+rename 保证。
                    def _download(
                        url: str = url, target: Path = target, gunzip: bool = gunzip
                    ) -> None:
                        download_object_artifact(url, target, gunzip=gunzip)

                    run_with_retry(
                        _download,
                        retriable=(RuntimeError,),
                        base_seconds=_RETRY_BACKOFF_BASE_SECONDS,
                        max_attempts=_RETRY_MAX_ATTEMPTS,
                    )
                if not declared or sha256_file(target) == declared:
                    # 下载成功且 digest 匹配（或无声明按既有语义不校验放行）。
                    continue
            except (RuntimeError, OSError, EOFError) as exc:
                # 传输失败（重试耗尽，含归一化后的 urllib3 读时族）与解码
                # 失败（下载层已归一化为 RuntimeError；EOFError 留在集合
                # 是防御余量）统一进回落判定——任何一族都不得绕过两段式。
                presigned_error = exc
            if not declared:
                # ref 缺 sha256：无冻结身份可回落——下载失败原样上抛（此
                # 分支必然来自 except 臂，presigned_error 非 None）。
                assert presigned_error is not None
                raise presigned_error
            try:
                _download_cas(client, declared, target, name, download_slots)
            except (RuntimeError, OSError) as exc:
                # 失败归因：两段式报错——presigned 段为何失败（对象被覆
                # 盖/HTTP/解码）与 CAS 回落段为何也没救回来（404/GC、
                # transient 耗尽、CAS 自验失败、本地写盘）合并上抛。
                detail = (
                    "presigned GET delivered rewritten bytes"
                    if presigned_error is None
                    else f"presigned GET failed ({presigned_error})"
                )
                raise RuntimeError(
                    f"artifact digest mismatch: {name}: {detail} "
                    f"and the CAS fallback failed too: {exc}"
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
