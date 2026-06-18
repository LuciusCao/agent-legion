from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / (
    "server/app/workflows/skills/reading_analysis/review_distractors/scripts/validate_output.py"
)
FIXTURE_QUESTIONS = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/reading_analysis/questions_parsed.json"
)
FIXTURE_VALID_RAW = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_distractors_valid/distractors_raw.json"
)
FIXTURE_INVALID_RAW = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_distractors_invalid/distractors_raw.json"
)
FIXTURE_KEYWORDS = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_distractors_valid/keywords_reviewed.json"
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
        "source_artifact": "distractors_raw.json",
        "source_artifact_sha256": _sha256_file(raw_path),
        "checks": [
            {"code": "LOCATION", "item_id": "dist-1", "passed": True},
            {"code": "RELEVANCE", "item_id": "dist-1", "passed": True},
            {"code": "NON_NECESSITY", "item_id": "dist-1", "passed": True},
            {"code": "KEYWORD_CONFLICT", "item_id": "dist-1", "passed": True},
            {"code": "LOCATION", "item_id": "dist-2", "passed": True},
            {"code": "RELEVANCE", "item_id": "dist-2", "passed": True},
            {"code": "NON_NECESSITY", "item_id": "dist-2", "passed": True},
            {"code": "KEYWORD_CONFLICT", "item_id": "dist-2", "passed": True},
        ],
        "issues": [],
    }


def _make_failed_report(raw_path: Path, question_id: str = "Q100") -> dict:
    return {
        "status": "failed",
        "question_id": question_id,
        "source_artifact": "distractors_raw.json",
        "source_artifact_sha256": _sha256_file(raw_path),
        "checks": [
            {"code": "LOCATION", "item_id": "dist-1", "passed": True},
            {"code": "RELEVANCE", "item_id": "dist-1", "passed": True},
            {"code": "NON_NECESSITY", "item_id": "dist-1", "passed": True},
            {"code": "KEYWORD_CONFLICT", "item_id": "dist-1", "passed": True},
            {"code": "LOCATION", "item_id": "dist-2", "passed": True},
            {"code": "RELEVANCE", "item_id": "dist-2", "passed": True},
            {"code": "NON_NECESSITY", "item_id": "dist-2", "passed": True},
            {"code": "KEYWORD_CONFLICT", "item_id": "dist-2", "passed": False},
        ],
        "issues": [
            {
                "code": "KEYWORD_CONFLICT",
                "item_id": "dist-2",
                "message": "distractor source_text overlaps with keyword source_text",
                "evidence": "单程 1400km",
            }
        ],
    }


@pytest.fixture
def question() -> dict:
    data: dict = json.loads(FIXTURE_QUESTIONS.read_text(encoding="utf-8"))
    return data["questions"][0]


class TestReviewDistractorsValidator:
    def test_exact_copy_success(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_one_invalid_candidate_fails_review(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_failed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "KEYWORD_CONFLICT" in result.stderr or "keyword" in result.stderr.lower()

    def test_failed_review_has_no_reviewed_file(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        # Use valid raw data; the review itself declares failure for non-structural reasons
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_failed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        # do NOT create distractors_reviewed.json

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_failed_review_with_reviewed_file_rejected(
        self, tmp_path: Path, question: dict
    ) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_failed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        # incorrectly create reviewed file for a failed review
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "failed review must not produce a reviewed artifact" in result.stderr

    def test_deleting_invalid_candidate_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        # reviewed has fewer items than raw (mutation)
        reviewed = {
            "question_id": raw_data["question_id"],
            "distractors": [raw_data["distractors"][0]],
        }
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(reviewed, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reordering_candidates_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        # Reorder distractors
        reviewed = {
            "question_id": raw_data["question_id"],
            "distractors": list(reversed(raw_data["distractors"])),
        }
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(reviewed, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_changing_confusion_strength_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        reviewed = json.loads(json.dumps(raw_data))
        reviewed["distractors"][0]["confusion_strength"] = 99
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(reviewed, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_stale_sha256_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        report["source_artifact_sha256"] = "0" * 64
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "SHA-256" in result.stderr

    def test_keyword_overlap_not_semantic_plausibility(
        self, tmp_path: Path, question: dict
    ) -> None:
        """A distractor that is semantically plausible but overlaps with an approved keyword
        must be rejected by the validator, not slip through on plausibility alone."""
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        keywords = json.loads(FIXTURE_KEYWORDS.read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(keywords, ensure_ascii=False), encoding="utf-8"
        )
        # Build a raw with a keyword-overlap distractor, then claim "passed"
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "distractors_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "keyword" in result.stderr.lower() and "overlap" in result.stderr.lower()
