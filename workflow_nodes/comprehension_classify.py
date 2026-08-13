"""question_comprehension_info node: classify comprehension eligibility.

Heuristic gate: questions that are pure calculation drills or multiple
choice have no standalone comprehension information, so they are marked
ineligible and the workflow short-circuits to the finalize node.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.workflows.comprehension_common import _single_parsed_question
from workspace_libs.node_sdk import NodeContext


def _looks_like_pure_calculation(stem: str, options: list[Any] | None = None) -> bool:
    compact = "".join(stem.split())
    calculation_markers = ("计算：", "计算:", "=", "＝")
    has_marker = any(marker in compact for marker in calculation_markers)
    has_digits = any(ch.isdigit() for ch in compact)
    options = options or []
    has_short_options = len(options) <= 4
    return has_marker and has_digits and has_short_options and len(compact) <= 32


def _looks_like_multiple_choice(options: list[Any] | None = None) -> bool:
    return len(options or []) >= 2


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    source_id = str(job["source_id"])
    question = _single_parsed_question(job_dir, source_id)
    stem = str(question.get("stem") or "")
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if not stem.strip():
        payload = {
            "question_id": source_id,
            "eligible": False,
            "reason_code": "empty_stem",
            "reason": "题干为空，无有效审题内容。",
        }
    elif _looks_like_pure_calculation(stem, options):
        payload = {
            "question_id": source_id,
            "eligible": False,
            "reason_code": "pure_calculation",
            "reason": "题目主要考查直接计算，没有独立于解题步骤的审题信息。",
        }
    elif _looks_like_multiple_choice(options):
        payload = {
            "question_id": source_id,
            "eligible": False,
            "reason_code": "multiple_choice",
            "reason": "选择题为选项式作答，不适合生成独立审题信息。",
        }
    else:
        payload = {
            "question_id": source_id,
            "eligible": True,
            "reason_code": "eligible",
            "reason": "题目可能包含独立审题信息，继续生成。",
        }
    ctx.artifacts.write_json("comprehension_eligibility.json", payload)
