"""Shared bundle/artifact IO for Worker execution preparation.

Split out of ``worker.execution_prepare`` for the file-size budget: the agent
path (``prepare_execution``) and the batch-2 code path (``code_runner``) both
download a tar.gz bundle and a set of content-addressed input artifacts, and
both apply the same untrusted-archive rules.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from worker._retry import run_with_retry
from worker.artifact_download import download_object_artifact
from worker.host_client import Client

# 与 host_transfer 同一 retry 语义：transient 失败指数退避，上限 3 次。
_RETRY_BACKOFF_BASE_SECONDS = 1.0
_RETRY_MAX_ATTEMPTS = 3


def safe_extract_tree(archive: Path, destination: Path) -> None:
    """Extract a tar.gz bundle, rejecting absolute/parent/link members."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.islnk() or member.issym():
                raise ValueError(f"unsafe Agent bundle member: {member.name!r}")
        tar.extractall(destination, filter="data")


def safe_extract(archive: Path, destination: Path) -> dict[str, Any]:
    """Agent bundles carry a manifest.json; code bundles deliberately do not."""
    safe_extract_tree(archive, destination)
    return json.loads((destination / "manifest.json").read_text(encoding="utf-8"))


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

    Value forms (#160 D12): a ``{"url", "sha256"}`` dict downloads straight
    from object storage (presigned GET, sha256 verified when declared); the
    legacy ``"sha256:<hash>"`` string keeps the Host CAS channel.
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
            with download_slots:
                # 与 CAS 分支对齐：信号量限流 + 退避重试；每次重试重新打开
                # 下载流，.part 截断重写由 download_object_artifact 的
                # temp+rename 保证。
                run_with_retry(
                    lambda url=url, target=target: download_object_artifact(url, target),
                    retriable=(RuntimeError,),
                    base_seconds=_RETRY_BACKOFF_BASE_SECONDS,
                    max_attempts=_RETRY_MAX_ATTEMPTS,
                )
            declared = str(ref.get("sha256") or "")
            if declared and sha256_file(target) != declared:
                raise RuntimeError(f"artifact digest mismatch: {name}")
            continue
        digest = str(ref).split(":", 1)[-1]
        with download_slots:
            client.download(f"/api/artifacts/{digest}", target)
        if sha256_file(target) != digest:
            raise RuntimeError(f"artifact digest mismatch: {name}")
