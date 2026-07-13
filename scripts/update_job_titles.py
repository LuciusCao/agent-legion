#!/usr/bin/env python3
"""Update job titles and stems from CMS for existing question jobs."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
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

    with closing(sqlite3.connect(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, source_id, title, stem FROM jobs WHERE source_type = 'question'")
        jobs = cursor.fetchall()
        print(f"Found {len(jobs)} question jobs")

        updated = 0
        skipped = 0
        failed = 0

        for job_id, source_id, old_title, old_stem in jobs:
            try:
                payload = _fetch_json(api_url, {"uuid": source_id}, token, timeout=15)
                data = _parse_question_detail_payload(payload)

                if data is None:
                    print(f"  [{source_id}] CMS not found, skipping")
                    skipped += 1
                    continue

                new_title = _question_title_from_item(data)
                body = data.get("body")
                new_stem = ""
                if isinstance(body, dict):
                    new_stem = str(body.get("content") or "").strip()

                title_changed = new_title != old_title
                stem_changed = new_stem != old_stem
                if not title_changed and not stem_changed:
                    print(f"  [{source_id}] unchanged")
                    skipped += 1
                    continue

                cursor.execute(
                    "UPDATE jobs SET title = ?, stem = ? WHERE id = ?",
                    (new_title, new_stem, job_id),
                )
                conn.commit()
                changes = []
                if title_changed:
                    changes.append(
                        f"    title: {old_title[:40]}{'...' if len(old_title) > 40 else ''}"
                        f" -> {new_title[:40]}{'...' if len(new_title) > 40 else ''}"
                    )
                if stem_changed:
                    changes.append(
                        f"    stem: {old_stem[:40]}{'...' if len(old_stem) > 40 else ''}"
                        f" -> {new_stem[:40]}{'...' if len(new_stem) > 40 else ''}"
                    )
                print(f"  [{source_id}] updated:\n" + "\n".join(changes))
                updated += 1
            except Exception as exc:
                print(f"  [{source_id}] error: {exc}")
                failed += 1

    print(f"\nDone: {updated} updated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
