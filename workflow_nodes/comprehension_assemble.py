"""question_comprehension_info node: assemble the final comprehension payload.

Validates the reviewed agent artifacts (key info, possible errors,
difficulty) against the parsed question, then writes
``comprehension_info.json`` and the job ``manifest.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.executors.cancellation import check_cancellation
from server.app.workflows.comprehension_common import (
    _assert_artifact_question_id,
    _load_json_object,
    _single_parsed_question,
)
from server.app.workflows.skill_version_collection import collect_skill_versions
from server.app.workflows.workflow_manifest import workflow_manifest

logger = logging.getLogger(__name__)


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    context = runtime or {}
    source_id = str(job["source_id"])
    logger.info("assemble_comprehension_info: source_id=%s", source_id)
    question = _single_parsed_question(job_dir, source_id, "questions_parsed_lean.json")
    key_info = _load_json_object(job_dir / "key_info_reviewed.json")
    possible_errors = _load_json_object(job_dir / "possible_errors_reviewed.json")
    difficulty = _load_json_object(job_dir / "comprehension_difficulty.json")

    logger.info("  validating input artifacts")
    for name, content in (
        ("key_info_reviewed.json", key_info),
        ("possible_errors_reviewed.json", possible_errors),
        ("comprehension_difficulty.json", difficulty),
    ):
        check_cancellation(context)
        _assert_artifact_question_id(name, content, source_id)

    fingerprint = question.get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, str):
        raise ValueError("fingerprint must be a string or null")

    comprehension_data = {
        "fingerprint": fingerprint,
        "comprehension_difficulty": difficulty.get("comprehension_difficulty"),
        "key_info_list": key_info.get("key_info_list", []),
        "possible_error_list": possible_errors.get("possible_error_list", []),
    }
    payload = {
        "question_id": source_id,
        "fingerprint": fingerprint,
        "fingerprint_source": question.get("fingerprint_source", "missing"),
        "fingerprint_missing": fingerprint is None,
        "schema_version": "v1",
        "comprehension_data": comprehension_data,
    }
    manifest = {
        "question_id": source_id,
        "workflow_key": job.get("workflow_key", "question_comprehension_info"),
        "workflow": workflow_manifest(job, "question_comprehension_info"),
        "source_type": job.get("source_type", "question"),
        "title": job.get("title", ""),
        "fingerprint": fingerprint,
        "fingerprint_missing": fingerprint is None,
        "artifacts": {
            "comprehension_info.json": {"present": True},
        },
        "skill_versions": collect_skill_versions(str(job.get("id", "")), context, job),
    }

    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "comprehension_info.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (job_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("  wrote comprehension_info.json and manifest.json")
