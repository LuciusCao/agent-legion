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
from typing import Any, BinaryIO

import requests

from worker.host_client import Client

# Single socket timeout for presigned GET downloads; aligned with the
# transfer-timeout default of the bundle/artifact channel.
_DOWNLOAD_TIMEOUT_SECONDS = 120


def _open_download(url: str) -> BinaryIO:
    """Open a streaming reader for a presigned GET URL.

    Module-level seam: tests monkeypatch this instead of touching the
    network. The URL comes from the authenticated claim channel, so no SSRF
    guard applies here (same rule as worker.material_fetch).
    """
    response = requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
    if response.status_code != 200:
        response.close()
        raise RuntimeError(f"artifact download failed with HTTP {response.status_code}")
    return response.raw


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
            _download_object_artifact(str(ref.get("url") or ""), target)
            declared = str(ref.get("sha256") or "")
            if declared and sha256_file(target) != declared:
                raise RuntimeError(f"artifact digest mismatch: {name}")
            continue
        digest = str(ref).split(":", 1)[-1]
        with download_slots:
            client.download(f"/api/artifacts/{digest}", target)
        if sha256_file(target) != digest:
            raise RuntimeError(f"artifact digest mismatch: {name}")


def _download_object_artifact(url: str, target: Path) -> None:
    """Stream a presigned GET to an atomic temp+rename (same .part hygiene
    as the Host-channel download in worker.host_client)."""
    if not url:
        raise RuntimeError("input artifact is missing its download URL")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with _open_download(url) as stream, temporary.open("wb") as handle:
            while chunk := stream.read(1 << 20):
                handle.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
