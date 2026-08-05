from __future__ import annotations

import io
import json
import shutil
import tarfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any


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
