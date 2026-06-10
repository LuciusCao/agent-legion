#!/usr/bin/env python3
"""Update job titles from CMS knowledge names for existing question jobs."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server.app.cms.auth import _generate_prod_token
from server.app.cms.client import _fetch_json
from server.app.cms.question import _parse_question_detail_payload, _question_title_from_item


def _get_token() -> str | None:
    env = os.environ.get("BASECMS_ENV", "prod")
    token = os.environ.get("BASECMS_TOKEN")
    if token:
        return token.strip()
    config = {
        "token_gen": {
            "app_id": os.environ.get("BASECMS_APP_ID"),
            "nonce": os.environ.get("BASECMS_NONCE"),
            "secret": os.environ.get("BASECMS_SECRET"),
            "url": os.environ.get("BASECMS_TOKEN_URL"),
        }
    }
    if env == "prod":
        return _generate_prod_token(config)
    return None


def main() -> None:
    data_dir = Path(__file__).parent.parent / "data"
    db_path = data_dir / "video_hive.sqlite"

    token = _get_token()
    if not token:
        print("Failed to get CMS token")
        sys.exit(1)

    api_url = "http://cms.internal.example.com/v2/question/detail?bank_version=v5&country_id=1&subject_id=2"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id, source_id, title FROM jobs WHERE source_type = 'question'")
    jobs = cursor.fetchall()
    print(f"Found {len(jobs)} question jobs")

    updated = 0
    skipped = 0
    failed = 0

    for job_id, source_id, old_title in jobs:
        try:
            payload = _fetch_json(api_url, {"uuid": source_id}, token, timeout=15)
            data = _parse_question_detail_payload(payload)

            if data is None:
                print(f"  [{source_id}] CMS not found, skipping")
                skipped += 1
                continue

            new_title = _question_title_from_item(data)

            if new_title == old_title:
                print(f"  [{source_id}] unchanged: {new_title[:50]}")
                skipped += 1
                continue

            cursor.execute(
                "UPDATE jobs SET title = ? WHERE id = ?",
                (new_title, job_id),
            )
            conn.commit()
            print(
                f"  [{source_id}] updated:\n"
                f"    old: {old_title[:60]}{'...' if len(old_title) > 60 else ''}\n"
                f"    new: {new_title[:60]}{'...' if len(new_title) > 60 else ''}"
            )
            updated += 1

        except Exception as exc:
            print(f"  [{source_id}] error: {exc}")
            failed += 1

    conn.close()
    print(f"\nDone: {updated} updated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
