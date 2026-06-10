from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / (
    "server/app/pipelines/skills/reading_analysis/review_difficulty/scripts/validate_output.py"
)
FIXTURE_QUESTIONS = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/reading_analysis/questions_parsed.json"
)
FIXTURE_VALID_RAW = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_difficulty_valid/difficulty_raw.json"
)
FIXTURE_INVALID_RAW = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_difficulty_invalid/difficulty_raw.json"
)


def _run_validator(job_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(job_dir)],
        capture_output=True,
        text=True,
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _make_passed_report(raw_path: Path, question_id: str = "Q100") -> dict:
    return {
        "status": "passed",
        "question_id": question_id,
        "source_artifact": "difficulty_raw.json",
        "source_artifact_sha256": _sha256_file(raw_path),
        "checks": [],
        "issues": [],
    }


def _make_failed_report(raw_path: Path, question_id: str = "Q100") -> dict:
    return {
        "status": "failed",
        "question_id": question_id,
        "source_artifact": "difficulty_raw.json",
        "source_artifact_sha256": _sha256_file(raw_path),
        "checks": [],
        "issues": [
            {
                "code": "ARITHMETIC_MISMATCH",
                "field": "reading_difficulty",
                "message": "reading_difficulty does not match weighted sum",
                "evidence": "expected 47, got 48",
            }
        ],
    }


@pytest.fixture
def question() -> dict:
    data: dict = json.loads(FIXTURE_QUESTIONS.read_text(encoding="utf-8"))
    question_data: dict = data["questions"][0]
    return question_data


class TestReviewDifficultyValidator:
    def test_passed_review_cms_projection(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {
                    "question_id": raw_data["question_id"],
                    "reading_difficulty": raw_data["reading_difficulty"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_reject_extra_fields_in_reviewed(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {
                    "question_id": raw_data["question_id"],
                    "reading_difficulty": raw_data["reading_difficulty"],
                    "extra_field": "should_not_be_here",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "projection" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_changed_reading_difficulty(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {"question_id": raw_data["question_id"], "reading_difficulty": 99},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "projection" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_recalculated_with_different_weights(
        self, tmp_path: Path, question: dict
    ) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        # Recalculate with equal weights: round((50+50+40+45)/4) = round(46.25) = 46
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {"question_id": raw_data["question_id"], "reading_difficulty": 46},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "projection" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_stale_sha256(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        report["source_artifact_sha256"] = "0" * 64
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {
                    "question_id": raw_data["question_id"],
                    "reading_difficulty": raw_data["reading_difficulty"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "SHA-256" in result.stderr

    def test_reject_failed_with_reviewed_present(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_failed_report(raw_path)
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {
                    "question_id": raw_data["question_id"],
                    "reading_difficulty": raw_data["reading_difficulty"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "failed review must not produce a reviewed artifact" in result.stderr

    def test_reject_invalid_raw_arithmetic(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {"question_id": raw_data["question_id"], "reading_difficulty": 47},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "reading_difficulty mismatch" in result.stderr

    def test_reject_passed_with_issues(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(
                {"question_id": "Q100", "keywords": [{"source_text": "单程 1400km"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "difficulty_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        report["issues"] = [
            {
                "code": "ARITHMETIC_MISMATCH",
                "field": "reading_difficulty",
                "message": "reading_difficulty does not match weighted sum",
                "evidence": "expected 47, got 48",
            }
        ]
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {
                    "question_id": raw_data["question_id"],
                    "reading_difficulty": raw_data["reading_difficulty"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "passed report must contain no issues" in result.stderr
