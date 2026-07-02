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


def _looks_like_pure_calculation(stem: str, options: list[Any] | None = None) -> bool:
    compact = "".join(stem.split())
    calculation_markers = ("计算：", "计算:", "=", "＝")
    has_marker = any(marker in compact for marker in calculation_markers)
    has_digits = any(ch.isdigit() for ch in compact)
    options = options or []
    has_short_options = len(options) <= 4
    return has_marker and has_digits and has_short_options and len(compact) <= 32


def classify_comprehension_eligibility(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    source_id = str(job["source_id"])
    question = _single_parsed_question(artifact_dir, source_id)
    stem = str(question.get("stem") or "")
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if _looks_like_pure_calculation(stem, options):
        payload = {
            "question_id": source_id,
            "eligible": False,
            "reason_code": "pure_calculation",
            "reason": "题目主要考查直接计算，没有独立于解题步骤的审题信息。",
        }
    else:
        payload = {
            "question_id": source_id,
            "eligible": True,
            "reason_code": "eligible",
            "reason": "题目可能包含独立审题信息，继续生成。",
        }
    artifact_dir.joinpath("comprehension_eligibility.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def finalize_non_uploadable(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    source_id = str(job["source_id"])
    question = _single_parsed_question(artifact_dir, source_id)
    eligibility = _load_json_object(artifact_dir / "comprehension_eligibility.json")
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
        "skill_versions": collect_skill_versions(str(job.get("id", "")), context),
    }
    artifact_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
