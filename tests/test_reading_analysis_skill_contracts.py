from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_SHARED_ROOT = Path.home() / ".agents/skills/agent-legion/reading_analysis/extract_keywords/_shared"
sys.path.insert(0, str(_SHARED_ROOT))

from review import validate_review_result  # noqa: E402
from validation import (  # noqa: E402
    ContractError,
    load_json_object,
    load_single_question,
    sha256_file,
    validate_confidence,
    validate_exact_json_copy,
    validate_question_id,
    validate_review_hash,
    validate_score_1_99,
    validate_source_location,
    validate_unique_ids,
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


def test_validate_location_matches_option_text() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [{"key": "A", "text": "选项A"}],
    }
    location = {"source": "option", "option_key": "A", "start": 0, "end": 3}

    validate_source_location(question, "选项A", location)


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


@pytest.mark.parametrize(
    "payload",
    [1, "string", [], True, None],
)
def test_load_json_object_rejects_non_object(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "data.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    expected = f"Expected JSON object in {path}, got {type(payload).__name__}"

    with pytest.raises(ContractError, match=re.escape(expected)):
        load_json_object(path)


def test_load_single_question_returns_the_only_question(tmp_path: Path) -> None:
    path = tmp_path / "questions_parsed.json"
    path.write_text(
        json.dumps({"questions": [{"question_id": "Q1", "stem": "s"}]}),
        encoding="utf-8",
    )

    result = load_single_question(path)

    assert result == {"question_id": "Q1", "stem": "s"}


@pytest.mark.parametrize("questions", ["not-a-list", 123, {"foo": "bar"}])
def test_load_single_question_rejects_non_list_questions(tmp_path: Path, questions: object) -> None:
    path = tmp_path / "questions_parsed.json"
    path.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    expected = f"Expected exactly one question in {path}, found {type(questions).__name__}"

    with pytest.raises(ContractError, match=re.escape(expected)):
        load_single_question(path)


def test_load_single_question_rejects_non_dict_question(tmp_path: Path) -> None:
    path = tmp_path / "questions_parsed.json"
    path.write_text(json.dumps({"questions": ["not-a-dict"]}), encoding="utf-8")
    expected = f"Expected question dict in {path}, got str"

    with pytest.raises(ContractError, match=re.escape(expected)):
        load_single_question(path)


def test_validate_question_id_rejects_mismatch() -> None:
    payload = {"question_id": "Q2"}
    question = {"question_id": "Q1"}

    with pytest.raises(
        ContractError, match=re.escape("question_id mismatch: payload='Q2', source='Q1'")
    ):
        validate_question_id(payload, question)


def test_validate_source_location_rejects_unknown_option_key() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [{"key": "A", "text": "选项A"}],
    }

    with pytest.raises(
        ContractError,
        match=re.escape("location.option_key 'B' not found in question options"),
    ):
        validate_source_location(
            question,
            "选项A",
            {"source": "option", "option_key": "B", "start": 0, "end": 2},
        )


@pytest.mark.parametrize(
    ("items", "prefix", "expected_message"),
    [
        ([{}], "keyword", "keyword item missing string 'id': {}"),
        ([{"id": 1}], "keyword", "keyword item missing string 'id': {'id': 1}"),
        ([{"id": "a"}, {"id": "a"}], "distractor", "distractor duplicate id: 'a'"),
    ],
)
def test_validate_unique_ids_rejects_invalid_or_duplicate_ids(
    items: list[dict[str, object]],
    prefix: str,
    expected_message: str,
) -> None:
    with pytest.raises(ContractError, match=re.escape(expected_message)):
        validate_unique_ids(items, prefix)


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("high", "confidence must be a number, got str"),
        (1.1, "confidence must be in [0, 1], got 1.1"),
        (-0.1, "confidence must be in [0, 1], got -0.1"),
    ],
)
def test_validate_confidence_rejects_invalid_values(value: object, expected_message: str) -> None:
    with pytest.raises(ContractError, match=re.escape(expected_message)):
        validate_confidence(value)


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("x", "score must be an int, got str"),
        (True, "score must be an int, got bool"),
        (1.5, "score must be an int, got float"),
        (0, "score must be in [1, 99], got 0"),
        (100, "score must be in [1, 99], got 100"),
    ],
)
def test_validate_score_1_99_rejects_invalid_values(value: object, expected_message: str) -> None:
    with pytest.raises(ContractError, match=re.escape(expected_message)):
        validate_score_1_99(value, "score")


def test_validate_review_hash_rejects_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"question_id":"Q1"}', encoding="utf-8")
    expected = sha256_file(source)
    report = {"source_artifact_sha256": "bad-sha"}

    with pytest.raises(
        ContractError,
        match=re.escape(f"SHA-256 mismatch: expected {expected}, got 'bad-sha'"),
    ):
        validate_review_hash(source, report)


@pytest.mark.parametrize("status", [None, "", "ok"])
def test_validate_review_result_rejects_invalid_status(tmp_path: Path, status: object) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": status,
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    expected = f"report status must be 'passed' or 'failed', got {status!r}"
    with pytest.raises(ContractError, match=re.escape(expected)):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_failed_requires_issues(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError, match=re.escape("failed report must contain at least one issue")
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_passed_must_have_no_issues(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [{"code": "ISSUE", "message": "bad"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed_path.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match=re.escape("passed report must contain no issues")):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_projection_mismatch_when_not_exact_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100", "extra": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed_path.write_text(
        json.dumps({"question_id": "Q100", "extra": 2}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError,
        match=re.escape("reviewed artifact does not match the expected projection"),
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=False,
            projection=lambda raw: {"question_id": raw["question_id"], "extra": 99},
        )


def test_validate_review_result_rejects_report_question_id_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q200",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError,
        match=re.escape("report question_id mismatch: report='Q200', source='Q100'"),
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_rejects_non_array_checks(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": "not-a-list",
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError,
        match=re.escape("report checks must be an array, got str"),
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_rejects_non_array_issues(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": "not-a-list",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError,
        match=re.escape("report issues must be an array, got str"),
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_failed_rejects_reviewed_artifact(tmp_path: Path) -> None:
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
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [{"code": "TEST", "message": "fail"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed_path.write_text(
        json.dumps({"question_id": "Q100"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError,
        match=re.escape("failed review must not produce a reviewed artifact"),
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_exact_copy_rejects_mutation(tmp_path: Path) -> None:
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
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed_path.write_text(
        json.dumps({"question_id": "Q100", "keywords": [{"id": "added"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ContractError,
        match=re.escape("reviewed artifact must be an exact copy of the source artifact"),
    ):
        validate_review_result(
            source_path=source,
            reviewed_path=reviewed_path,
            report_path=report_path,
            exact_copy=True,
        )


def test_validate_review_result_projection_matches_when_not_exact_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100", "extra": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed_path.write_text(
        json.dumps({"question_id": "Q100", "extra": 1}, ensure_ascii=False),
        encoding="utf-8",
    )

    validate_review_result(
        source_path=source,
        reviewed_path=reviewed_path,
        report_path=report_path,
        exact_copy=False,
        projection=lambda raw: {"question_id": raw["question_id"], "extra": raw["extra"]},
    )


def test_validate_review_result_skips_reviewed_validation_without_projection(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    reviewed_path = tmp_path / "reviewed.json"

    source.write_text(
        json.dumps({"question_id": "Q100", "extra": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "question_id": "Q100",
                "source_artifact_sha256": sha256_file(source),
                "checks": [],
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed_path.write_text(
        json.dumps({"question_id": "Q100", "extra": 999}, ensure_ascii=False),
        encoding="utf-8",
    )

    validate_review_result(
        source_path=source,
        reviewed_path=reviewed_path,
        report_path=report_path,
        exact_copy=False,
        projection=None,
    )


def test_validate_source_location_rejects_non_int_start() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [],
    }

    with pytest.raises(
        ContractError,
        match=re.escape("location.start must be a non-negative int, got '0'"),
    ):
        validate_source_location(
            question,
            "题",
            {"source": "stem", "option_key": None, "start": "0", "end": 1},
        )


def test_validate_source_location_rejects_non_int_end() -> None:
    question = {
        "question_id": "Q100",
        "stem": "题干",
        "options": [],
    }

    with pytest.raises(
        ContractError,
        match=re.escape("location.end must be an int greater than start, got '1'"),
    ):
        validate_source_location(
            question,
            "题",
            {"source": "stem", "option_key": None, "start": 0, "end": "1"},
        )
