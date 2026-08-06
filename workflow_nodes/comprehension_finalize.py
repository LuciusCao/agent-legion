"""question_comprehension_info node: finalize a non-uploadable question.

Runs when the classify node marked the question ineligible: writes the final
``manifest.json`` with ``uploadable: False`` and the skip reason, so the job
completes without generating comprehension info.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.workflows.comprehension_common import (
    _assert_artifact_question_id,
    _load_json_object,
    _single_parsed_question,
)
from server.app.workflows.skill_version_collection import collect_skill_versions

logger = logging.getLogger(__name__)


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    context = runtime or {}
    source_id = str(job["source_id"])
    question = _single_parsed_question(job_dir, source_id)
    eligibility = _load_json_object(job_dir / "comprehension_eligibility.json")
    _assert_artifact_question_id("comprehension_eligibility.json", eligibility, source_id)
    fingerprint = question.get("fingerprint")
    manifest = {
        "question_id": source_id,
        "workflow_key": job.get("workflow_key", "question_comprehension_info"),
        "source_type": job.get("source_type", "question"),
        "title": job.get("title", ""),
        "fingerprint": fingerprint,
        "fingerprint_missing": fingerprint is None,
        "uploadable": False,
        "outcome": "non_uploadable",
        "skip_reason_code": eligibility.get("reason_code", ""),
        "skip_reason": eligibility.get("reason", ""),
        "artifacts": {
            "comprehension_info.json": {"present": False},
        },
        "skill_versions": collect_skill_versions(str(job.get("id", "")), context, job),
    }
    job_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
