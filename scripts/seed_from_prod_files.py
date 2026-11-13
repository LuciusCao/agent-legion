"""Legacy local job-dir and CAS compatibility copies for ``seed_from_prod``."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from scripts.seed_from_prod_db import SeedError


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def artifact_blob_relpath(hash_value: str) -> Path:
    digest = hash_value.strip()
    if not digest or "/" in digest or ".." in digest:
        raise SeedError(f"非法 artifact hash: {hash_value!r}")
    return Path("artifacts") / digest[:2] / digest


def job_storage_relpath(storage_dir: str, workspace_id: str, job_id: str) -> Path:
    rel = (storage_dir or "").strip() or f"jobs/{workspace_id}/{job_id}"
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        raise SeedError(f"非法 job storage_dir: {storage_dir!r}")
    return path


def copy_job_files(
    prod_data: Path, dev_data: Path, sample_jobs: list[dict[str, str]], dry_run: bool
) -> tuple[int, int]:
    dirs = 0
    total_bytes = 0
    for job in sample_jobs:
        rel = job_storage_relpath(job["storage_dir"], job["workspace_id"], job["id"])
        src = prod_data / rel
        dst = dev_data / rel
        if not src.is_dir():
            log(f"跳过缺失 job 目录: {src}")
            continue
        if dry_run:
            size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
            log(f"[dry-run] 将拷贝 {src} -> {dst} ({size / 1e6:.1f} MB)")
            dirs += 1
            total_bytes += size
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("jobs.sqlite"))
        dirs += 1
        total_bytes += sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    return dirs, total_bytes


def copy_artifact_blobs(
    prod_data: Path, dev_data: Path, hashes: Sequence[str], dry_run: bool
) -> tuple[int, int, int]:
    copied = 0
    total_bytes = 0
    missing = 0
    for digest in hashes:
        rel = artifact_blob_relpath(digest)
        src = prod_data / rel
        dst = dev_data / rel
        if not src.is_file():
            missing += 1
            continue
        if dry_run:
            copied += 1
            total_bytes += src.stat().st_size
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        total_bytes += dst.stat().st_size
    return copied, total_bytes, missing
