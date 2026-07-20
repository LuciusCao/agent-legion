from __future__ import annotations

import io
import json
import shutil
import tarfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any


class BundleError(ValueError):
    """Raised when a bundle or result archive is malformed or unsafe."""


def _validate_relative(name: str, *, what: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise BundleError(f"unsafe path in archive ({what}): {name!r}")
    return path


def _safe_members(tar: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in tar.getmembers():
        _validate_relative(member.name, what="member")
        if member.islnk() or member.issym():
            raise BundleError(f"links are not allowed in archives: {member.name!r}")
        yield member


def build_bundle(
    bundle_path: Path,
    *,
    skill_dir: Path | None = None,
    job_dir: Path,
    inputs: tuple[str, ...],
    manifest: dict[str, Any],
    skip_inputs: tuple[str, ...] = (),
) -> None:
    """Pack an optional skill snapshot, declared inputs, and the manifest into a tar.gz."""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    skip = set(skip_inputs)
    with tarfile.open(bundle_path, "w:gz") as tar:
        if skill_dir is not None:
            tar.add(skill_dir, arcname="skill")
        for rel in inputs:
            if rel in skip:
                continue
            rel_path = PurePosixPath(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise BundleError(f"unsafe input path: {rel!r}")
            src = job_dir / rel
            if not src.is_file():
                raise BundleError(f"declared input missing from job dir: {rel}")
            tar.add(src, arcname=f"inputs/{rel}")
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def extract_result_archive(archive_path: Path, job_dir: Path) -> None:
    """Extract a worker result archive into job_dir, rejecting unsafe entries."""
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in _safe_members(tar):
            target = job_dir / PurePosixPath(member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
