from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def fetch_question_context(job: dict[str, Any], artifact_dir: Path) -> None:
    payload = {
        "question_id": job["source_id"],
        "title": job["title"],
        "source_type": job["source_type"],
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "question_context.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
