from __future__ import annotations

import io
import json
import shutil
import tarfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

# Re-exported for the Host-side code bundle packer/consumers; the single
# copy lives in shared/code_sandbox.py (shared with the Worker runner).
from shared.code_sandbox import CODE_BUNDLE_LIBS_DIR as CODE_BUNDLE_LIBS_DIR
from shared.code_sandbox import CODE_BUNDLE_NODE_FILE as CODE_BUNDLE_NODE_FILE
from shared.code_sandbox import CODE_RESULT_LOG_MEMBER as CODE_RESULT_LOG_MEMBER


class AgentBundleError(ValueError):
    pass


def _safe_members(tar: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise AgentBundleError(f"unsafe path in Agent archive: {member.name!r}")
        if member.islnk() or member.issym():
            raise AgentBundleError(f"links are not allowed in Agent archives: {member.name!r}")
        yield member


@contextmanager
def cleanup_bundle_on_error(bundle_path: Path) -> Iterator[None]:
    try:
        yield
    except Exception:
        # #204 broad-except audit: compensate-then-bare-re-raise (#233
        # pattern). The with-block spans bundle build + broker enqueue, whose
        # outcome space is mixed on purpose — expected enqueue refusals,
        # storage staging failures, and programming errors all must delete
        # the half-built bundle file before propagating; the bare re-raise
        # preserves the original type so the caller's handling (and tests)
        # never see a converted failure. No logging here: the caller re-raises
        # with full context (the pool thread or dispatch path logs the
        # traceback), and the unlink failure mode (missing_ok) is silent by
        # design.
        bundle_path.unlink(missing_ok=True)
        raise


def build_agent_bundle(
    bundle_path: Path, *, skill_dir: Path | None, manifest: dict[str, Any]
) -> None:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "w:gz") as tar:
        if skill_dir is not None:
            tar.add(skill_dir, arcname="skill")
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def extract_agent_result(archive_path: Path, job_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in _safe_members(tar):
            target = job_dir / PurePosixPath(member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is not None:
                    with source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
