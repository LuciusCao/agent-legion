from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / (
    "server/app/pipelines/skills/reading_analysis/extract_keywords/scripts/validate_output.py"
)
FIXTURE_QUESTIONS = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/reading_analysis/questions_parsed.json"
)


def _run_validator(job_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(job_dir)],
        capture_output=True,
        text=True,
    )


def _make_valid_keywords_raw(question: dict) -> dict:
    stem = question["stem"]
    source_text = "单程 1400km"
    start = stem.index(source_text)
    end = start + len(source_text)
    return {
        "question_id": question["question_id"],
        "keywords": [
            {
                "id": "kw-1",
                "source_text": source_text,
                "normalized_text": "单程距离",
                "location": {"source": "stem", "option_key": None, "start": start, "end": end},
                "necessity": "决定路程计算的必要条件",
                "counterfactual": "删除距离后无法完成数值计算",
                "confidence": 0.96,
            }
        ],
    }


def _make_valid_report(question: dict) -> dict:
    return {
        "question_id": question["question_id"],
        "candidate_count": 1,
        "method": "counterfactual_deletion",
        "warnings": [],
    }


@pytest.fixture
def question() -> dict:
    data = json.loads(FIXTURE_QUESTIONS.read_text(encoding="utf-8"))
    return data["questions"][0]


class TestExtractKeywordsValidator:
    def test_valid_artifacts_pass(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        # Copy fixture
        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(_make_valid_keywords_raw(question), ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_missing_keywords_raw(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "keywords_raw.json" in result.stderr

    def test_missing_keywords_report(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(_make_valid_keywords_raw(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "keywords_report.json" in result.stderr

    def test_question_id_mismatch(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["question_id"] = "Q999"
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "question_id mismatch" in result.stderr

    def test_duplicate_keyword_ids(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"].append(raw["keywords"][0].copy())
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "duplicate id" in result.stderr

    def test_analysis_source_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"][0]["location"] = {
            "source": "analysis",
            "option_key": None,
            "start": 0,
            "end": 3,
        }
        raw["keywords"][0]["source_text"] = "往返距离"
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "stem or option" in result.stderr

    def test_location_mismatch(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"][0]["location"]["start"] = 0
        raw["keywords"][0]["location"]["end"] = 2
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "source_text mismatch" in result.stderr

    def test_empty_normalized_text(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"][0]["normalized_text"] = ""
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "normalized_text" in result.stderr

    def test_empty_necessity(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"][0]["necessity"] = "   "
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "necessity" in result.stderr

    def test_empty_counterfactual(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"][0]["counterfactual"] = ""
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "counterfactual" in result.stderr

    def test_confidence_out_of_range(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        raw["keywords"][0]["confidence"] = 1.5
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "confidence" in result.stderr

    def test_missing_keyword_field(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )

        raw = _make_valid_keywords_raw(question)
        del raw["keywords"][0]["necessity"]
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "necessity" in result.stderr

    def test_report_question_id_mismatch(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(_make_valid_keywords_raw(question), ensure_ascii=False), encoding="utf-8"
        )

        report = _make_valid_report(question)
        report["question_id"] = "Q999"
        (job_dir / "keywords_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "question_id mismatch" in result.stderr

    def test_report_missing_candidate_count(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(_make_valid_keywords_raw(question), ensure_ascii=False), encoding="utf-8"
        )

        report = _make_valid_report(question)
        del report["candidate_count"]
        (job_dir / "keywords_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "candidate_count" in result.stderr
