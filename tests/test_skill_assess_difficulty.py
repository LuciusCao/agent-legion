from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / (
    "server/app/workflows/skills/reading_analysis/assess_difficulty/scripts/validate_output.py"
)
FIXTURE_QUESTIONS = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/reading_analysis/questions_parsed.json"
)
FIXTURE_SCENARIOS = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/difficulty_scenarios.json"
)


def _run_validator(job_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(job_dir)],
        capture_output=True,
        text=True,
    )


def _make_valid_difficulty_raw(question: dict) -> dict:
    return {
        "question_id": question["question_id"],
        "dimensions": {
            "knowledge_complexity": 42,
            "reasoning_steps": 55,
            "calculation_load": 28,
            "reading_filter_load": 61,
        },
        "weights": {
            "knowledge_complexity": 0.3,
            "reasoning_steps": 0.3,
            "calculation_load": 0.2,
            "reading_filter_load": 0.2,
        },
        "reading_difficulty": 47,
        "evidence": {
            "knowledge_complexity": ["使用一次乘法模型"],
            "reasoning_steps": ["识别往返后乘以 2"],
            "calculation_load": ["1400 × 2"],
            "reading_filter_load": ["需要区分城市名称与距离条件"],
        },
    }


def _make_valid_report(question: dict) -> dict:
    return {
        "question_id": question["question_id"],
        "formula": "round(weighted_sum)",
        "weighted_sum": 46.9,
        "reading_difficulty": 47,
        "warnings": [],
    }


@pytest.fixture
def question() -> dict:
    data = json.loads(FIXTURE_QUESTIONS.read_text(encoding="utf-8"))
    return data["questions"][0]


class TestAssessDifficultyValidator:
    def test_valid_artifacts_pass(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(_make_valid_difficulty_raw(question), ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_missing_dimension_keys(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        del raw["dimensions"]["calculation_load"]
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "dimensions" in result.stderr

    def test_non_integer_scores(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        raw["dimensions"]["knowledge_complexity"] = 42.5
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "int" in result.stderr

    def test_score_outside_range(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        raw["dimensions"]["knowledge_complexity"] = 0
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "[1, 99]" in result.stderr

    def test_weight_outside_range(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        raw["weights"]["knowledge_complexity"] = 1.5
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "[0, 1]" in result.stderr

    def test_weights_sum_not_one(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        raw["weights"]["knowledge_complexity"] = 0.5
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "sum to 1.0" in result.stderr

    def test_empty_evidence(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        raw["evidence"]["calculation_load"] = []
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "non-empty" in result.stderr

    def test_incorrect_reading_difficulty(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_difficulty_raw(question)
        raw["reading_difficulty"] = 50
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        report = _make_valid_report(question)
        report["reading_difficulty"] = 50
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "reading_difficulty mismatch" in result.stderr


class TestDifficultyCalculation:
    def test_expected_difficulty_from_scenarios(self) -> None:
        sys.path.insert(0, str(VALIDATOR.parent))
        from validate_output import (  # type: ignore[import-not-found]  # noqa: E402, I001
            calculate_reading_difficulty,
        )

        scenarios = json.loads(FIXTURE_SCENARIOS.read_text(encoding="utf-8"))
        dimensions = {
            "knowledge_complexity": 42,
            "reasoning_steps": 55,
            "calculation_load": 28,
            "reading_filter_load": 61,
        }
        for scenario in scenarios:
            weights = scenario["weights"]
            expected = scenario["expected_difficulty"]
            actual = calculate_reading_difficulty(dimensions, weights)
            assert actual == expected, (
                f"Scenario {scenario['name']}: expected {expected}, got {actual}"
            )
