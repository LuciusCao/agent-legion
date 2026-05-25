#!/usr/bin/env python3
"""Backfill source_uuid for existing videos from CMS API.

Usage:
    python scripts/backfill_source_uuid.py
    python scripts/backfill_source_uuid.py --dry-run
    python scripts/backfill_source_uuid.py --env prod
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app.cms.client import get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.cms.question import lookup_question_video
from server.app.db import Database
from server.app.settings import load_settings


def main():
    parser = argparse.ArgumentParser(description="Backfill source_uuid from CMS")
    parser.add_argument("--env", default="prod", help="CMS environment")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be updated without writing")
    args = parser.parse_args()

    settings = load_settings()
    db = Database(settings.db_path if hasattr(settings, "db_path") else settings.data_dir / "video_hive.sqlite")
    cms = settings.config.get("cms", {})

    if not cms:
        print("[Error] CMS config missing in config/pipeline.yaml")
        return 1

    token = get_token(args.env, cms)
    if not token:
        print("[Error] Failed to get CMS token")
        return 1

    videos = [v for v in db.list_videos() if not v.get("source_uuid")]
    if not videos:
        print("No videos missing source_uuid.")
        return 0

    print(f"Found {len(videos)} videos missing source_uuid")
    print(f"{'ID':<30} {'Type':<10} {'External ID':<20} {'Status':<12} {'Result'}")
    print("-" * 80)

    updated = 0
    skipped = 0
    errors = 0

    for video in videos:
        vid = video["id"]
        ctype = video["content_type"]
        ext = video["external_id"]
        status = video["status"]

        if not ext:
            print(f"{vid:<30} {ctype:<10} {ext:<20} {status:<12} SKIP: no external_id")
            skipped += 1
            continue

        try:
            if ctype == "knowledge":
                lookup = lookup_knowledge_video(ext, cms.get("knowledge_url"), token)
            else:
                lookup = lookup_question_video(ext, cms.get("question_url"), token)
        except Exception as exc:
            print(f"{vid:<30} {ctype:<10} {ext:<20} {status:<12} ERROR: {exc}")
            errors += 1
            continue

        source_uuid = lookup.source_uuid or ""
        if not source_uuid:
            print(f"{vid:<30} {ctype:<10} {ext:<20} {status:<12} SKIP: CMS returned empty source_uuid")
            skipped += 1
            continue

        if args.dry_run:
            print(f"{vid:<30} {ctype:<10} {ext:<20} {status:<12} WOULD UPDATE -> {source_uuid}")
        else:
            db.update_video(vid, source_uuid=source_uuid)
            print(f"{vid:<30} {ctype:<10} {ext:<20} {status:<12} UPDATED -> {source_uuid}")
        updated += 1

    print("-" * 80)
    print(f"Total: {len(videos)} | Updated: {updated} | Skipped: {skipped} | Errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
