"""Unit tests for workflow_nodes/comprehension_classify.py."""

from __future__ import annotations

import json
from pathlib import Path

from workflow_nodes import comprehension_classify


def _job(source_id: str = "q-1") -> dict[str, str]:
    return {"id": "job-1", "workspace_id": "ws-a", "source_id": source_id, "title": "t"}


def _write_parsed(
    job_dir: Path, source_id: str, stem: str, options: list[str] | None = None
) -> None:
    payload = {
        "questions": [
            {
                "question_id": source_id,
                "stem": stem,
                "options": options or [],
                "answer": "",
                "analysis": "",
            }
        ]
    }
    job_dir.joinpath("questions_parsed.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _read_result(job_dir: Path) -> dict[str, object]:
    return json.loads(job_dir.joinpath("comprehension_eligibility.json").read_text("utf-8"))


def test_blank_stem_is_ineligible(tmp_path: Path) -> None:
    _write_parsed(tmp_path, "q-1", "   ")

    comprehension_classify.run(_job(), tmp_path, {})

    result = _read_result(tmp_path)
    assert result["eligible"] is False
    assert result["reason_code"] == "empty_stem"


def test_pure_calculation_is_ineligible(tmp_path: Path) -> None:
    _write_parsed(tmp_path, "q-1", "计算：3+5=?", ["6", "8"])

    comprehension_classify.run(_job(), tmp_path, {})

    result = _read_result(tmp_path)
    assert result["eligible"] is False
    assert result["reason_code"] == "pure_calculation"


def test_word_problem_is_eligible(tmp_path: Path) -> None:
    _write_parsed(tmp_path, "q-1", "小明有 3 个苹果，又买来 5 个，一共有几个苹果？")

    comprehension_classify.run(_job(), tmp_path, {})

    result = _read_result(tmp_path)
    assert result["eligible"] is True
    assert result["reason_code"] == "eligible"
