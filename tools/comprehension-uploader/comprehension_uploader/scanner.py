from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from comprehension_uploader.config import Config
from comprehension_uploader.db import Database
from comprehension_uploader.fingerprint import compute_question_fingerprint
from comprehension_uploader.question_source import QuestionSource

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(
        self,
        config: Config,
        db: Database,
        source: QuestionSource,
    ) -> None:
        self.config = config
        self.db = db
        self.source = source

    def scan(self, output_path: str | None = None) -> dict[str, Any]:
        states = self.db.states.get_all()
        total = len(states)
        stale = 0
        failed = 0
        stale_items: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        for row in states:
            question_id = row["question_id"]
            old_fingerprint = row["latest_fingerprint"]
            try:
                latest = self.source.get_latest(question_id)
                if not latest:
                    self.db.states.update_scan(question_id, now)
                    continue

                stem = latest.get("stem")
                options = latest.get("options")
                if stem is None or options is None:
                    self.db.states.update_scan(question_id, now)
                    continue

                new_fingerprint = compute_question_fingerprint(stem, options)
                if new_fingerprint is None:
                    self.db.states.update_scan(question_id, now)
                    continue

                if new_fingerprint != old_fingerprint:
                    stale += 1
                    reason = "fingerprint_changed"
                    self.db.states.update_scan(question_id, now, stale_reason=reason)
                    self.db.scan_results.insert(
                        question_id,
                        old_fingerprint,
                        new_fingerprint,
                        now,
                    )
                    stale_items.append(
                        {
                            "question_id": question_id,
                            "old_fingerprint": old_fingerprint,
                            "new_fingerprint": new_fingerprint,
                            "latest_upload_at": row["updated_at"],
                        }
                    )
                else:
                    self.db.states.update_scan(question_id, now, stale_reason=None)
            except Exception as exc:  # noqa: BLE001
                logger.exception("scan failed for %s", question_id)
                failed += 1
                self.db.states.update_scan(question_id, now, stale_reason=f"source_error: {exc}")

        if output_path:
            Path(output_path).write_text(
                json.dumps(stale_items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return {
            "total": total,
            "stale": stale,
            "failed": failed,
            "items": stale_items,
        }
