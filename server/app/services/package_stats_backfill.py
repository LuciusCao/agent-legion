"""Backfill stats for legacy packages created before stats were persisted."""

from __future__ import annotations

import json
import logging
import zipfile

from server.app.db import Database
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_data_path

logger = logging.getLogger(__name__)


def backfill_package_stats(db: Database, settings: Settings) -> int:
    """Persist missing name/video_count/size_bytes for legacy package rows.

    Rows inserted before package stats were recorded at creation time are
    healed here at app startup so that GET /packages stays read-only.
    Returns the number of rows updated; never raises.
    """
    try:
        resolved_packages_dir = settings.packages_dir.resolve(strict=True)
        packages = db.list_packages(limit=1000)
    except Exception:
        logger.exception("Package stats backfill failed to start")
        return 0

    updated = 0
    for pkg in packages:
        if pkg.get("name") and pkg.get("video_count", 0) != 0:
            continue
        stored_path = pkg.get("path") or ""
        if not stored_path:
            continue
        try:
            resolved_path = resolve_data_path(stored_path, settings.data_dir, allow_missing=True)
            if resolved_path == resolved_packages_dir or not resolved_path.is_relative_to(
                resolved_packages_dir
            ):
                raise ManagedPathError("Path escapes package root")
        except ManagedPathError as exc:
            logger.warning("Skip package stats backfill for %s: %s", pkg.get("id"), exc)
            continue
        try:
            with zipfile.ZipFile(resolved_path) as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            video_count = len(manifest.get("videos", []))
            name = pkg.get("name") or f"批次 ({video_count}个视频)"
            size_bytes = pkg.get("size_bytes") or resolved_path.stat().st_size
            db.update_package_stats(
                pkg["id"], name=name, video_count=video_count, size_bytes=size_bytes
            )
        except Exception as exc:
            logger.warning(
                "Cannot backfill stats for package %s (%s): %s", pkg.get("id"), stored_path, exc
            )
            continue
        updated += 1
    if updated:
        logger.info("Backfilled stats for %s legacy packages", updated)
    return updated
