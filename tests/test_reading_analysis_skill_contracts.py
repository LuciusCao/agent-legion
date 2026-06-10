from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.pipelines.skills.reading_analysis._shared.review import validate_review_result
from server.app.pipelines.skills.reading_analysis._shared.validation import (
    ContractError,
    load_single_question,
    sha256_file,
    validate_exact_json_copy,
    validate_review_hash,
    validate_source_location,
)


def test_load_single_question_requires_exactly_one_question(tmp_path: Path) -> None:
    path = tmp_path / "questions_parsed.json"
    path.write_text('{"questions": []}', encoding="utf-8")

    with pytest.raises(ContractError, match="exactly one question"):
        load_single_question(path)


def test_validate_location_matches_stem_text() -> None:
    question = {
        "question_id": "Q100",
        "stem": "小明从上海开车去北京，单程 1400km",
        "options": [],
        "answer": "",
        "analysis": "",
    }
    location = {"source": "stem", "option_key": None, "start": 3, "end": 5}

    validate_source_location(question, "上海", location)


def test_validate_location_rejects_analysis_source() -> None:
    with pytest.raises(ContractError, match="stem or option"):
        validate_source_location(
            {"question_id": "Q100", "stem": "题干", "options": []},
            "解析词",
            {"source": "analysis", "option_key": None, "start": 0, "end": 3},
        )


def test_validate_location_rejects_missing_option_key() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [{"key": "A", "text": "选项A"}],
    }
    with pytest.raises(ContractError, match="option_key"):
        validate_source_location(
            question,
            "选项A",
            {"source": "option", "option_key": None, "start": 0, "end": 2},
        )


def test_validate_location_rejects_negative_start() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [],
    }
    with pytest.raises(ContractError, match="start"):
        validate_source_location(
            question,
            "题",
            {"source": "stem", "option_key": None, "start": -1, "end": 1},
        )


def test_validate_location_rejects_end_not_greater_than_start() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [],
    }
    with pytest.raises(ContractError, match="end"):
        validate_source_location(
            question,
            "",
            {"source": "stem", "option_key": None, "start": 2, "end": 2},
        )


def test_validate_location_rejects_out_of_range_offsets() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [],
    }
    with pytest.raises(ContractError, match="range"):
        validate_source_location(
            question,
            "太长",
            {"source": "stem", "option_key": None, "start": 0, "end": 10},
        )


def test_validate_location_rejects_mismatched_source_text() -> None:
    question = {
        "question_id": "Q100",
        "stem": "小明从上海开车去北京",
        "options": [],
    }
    with pytest.raises(ContractError, match="source_text"):
        validate_source_location(
            question,
            "北京",
            {"source": "stem", "option_key": None, "start": 2, "end": 4},
        )


def test_validate_exact_copy_rejects_review_mutation(tmp_path: Path) -> None:
    raw = tmp_path / "raw.json"
    reviewed = tmp_path / "reviewed.json"
    raw.write_text('{"question_id":"Q1","keywords":[]}', encoding="utf-8")
    reviewed.write_text('{"question_id":"Q1","keywords":[{"id":"added"}]}', encoding="utf-8")

    with pytest.raises(ContractError, match="exact copy"):
        validate_exact_json_copy(raw, reviewed)


def test_sha256_matches_review_report(tmp_path: Path) -> None:
    source = tmp_path / "raw.json"
    source.write_text('{"question_id":"Q1"}', encoding="utf-8")
    report = {"source_artifact_sha256": sha256_file(source)}

    validate_review_hash(source, report)


def test_failed_review_allows_reviewed_absence(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100", "keywords": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "question_id": "Q100",
                "source_artifact": "keywords_raw.json",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [{"code": "TEST", "message": "fail"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validate_review_result(
        source_path=source,
        reviewed_path=reviewed_path,
        report_path=report_path,
        exact_copy=True,
    )


def test_passed_review_requires_reviewed_presence(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100", "keywords": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact": "keywords_raw.json",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="reviewed artifact"):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )
