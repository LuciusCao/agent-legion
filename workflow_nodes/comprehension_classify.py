"""question_comprehension_info node: classify comprehension eligibility.

Heuristic gate: questions that are pure calculation drills have no
standalone comprehension information, so they are marked ineligible and the
workflow short-circuits to the finalize node.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.workflows.comprehension_common import _single_parsed_question

logger = logging.getLogger(__name__)


def _looks_like_pure_calculation(stem: str, options: list[Any] | None = None) -> bool:
    compact = "".join(stem.split())
    calculation_markers = ("计算：", "计算:", "=", "＝")
    has_marker = any(marker in compact for marker in calculation_markers)
    has_digits = any(ch.isdigit() for ch in compact)
    options = options or []
    has_short_options = len(options) <= 4
    return has_marker and has_digits and has_short_options and len(compact) <= 32


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    source_id = str(job["source_id"])
    question = _single_parsed_question(job_dir, source_id)
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
    job_dir.joinpath("comprehension_eligibility.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
