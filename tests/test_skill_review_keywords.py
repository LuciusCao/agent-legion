from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path.home() / (
    ".agents/skills/agent-legion/reading_analysis/review_keywords/scripts/validate_output.py"
)
FIXTURE_QUESTIONS = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/reading_analysis/questions_parsed.json"
)
FIXTURE_VALID_RAW = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_keywords_valid/keywords_raw.json"
)
FIXTURE_INVALID_RAW = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/review_keywords_invalid/keywords_raw.json"
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
        "source_artifact": "keywords_raw.json",
        "source_artifact_sha256": _sha256_file(raw_path),
        "checks": [],
        "issues": [],
    }


def _make_failed_report(raw_path: Path, question_id: str = "Q100") -> dict:
    return {
        "status": "failed",
        "question_id": question_id,
        "source_artifact": "keywords_raw.json",
        "source_artifact_sha256": _sha256_file(raw_path),
        "checks": [],
        "issues": [
            {
                "code": "NOT_NECESSARY",
                "item_id": "kw-2",
                "field": "necessity",
                "message": "删除该词组不影响解题路径",
                "evidence": "城市名称可以替换",
            }
        ],
    }


@pytest.fixture
def question() -> dict:
    data: dict = json.loads(FIXTURE_QUESTIONS.read_text(encoding="utf-8"))
    question_data: dict = data["questions"][0]
    return question_data


class TestReviewKeywordsValidator:
    def test_passed_review_requires_exact_copy(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_failed_review_keeps_report_and_forbids_reviewed_artifact(
        self, tmp_path: Path, question: dict
    ) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_failed_report(raw_path)
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_reject_changed_ordering(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        # Swap keyword order in reviewed artifact
        reordered = {
            "question_id": raw_data["question_id"],
            "keywords": list(reversed(raw_data["keywords"])),
        }
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(reordered, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_normalized_text_edit(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        edited = json.loads(json.dumps(raw_data))
        edited["keywords"][0]["normalized_text"] = "单程距离修改"
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(edited, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_removed_items(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        # Remove one keyword from reviewed
        removed = {
            "question_id": raw_data["question_id"],
            "keywords": [raw_data["keywords"][0]],
        }
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(removed, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_added_items(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        added = json.loads(json.dumps(raw_data))
        added["keywords"].append(
            {
                "id": "kw-3",
                "source_text": "北京",
                "normalized_text": "目的城市",
                "location": {"source": "stem", "option_key": None, "start": 7, "end": 9},
                "necessity": "必须知道目的城市",
                "counterfactual": "不知道目的城市无法解题",
                "confidence": 0.8,
            }
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(added, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "exact copy" in result.stderr.lower() or "reviewed artifact" in result.stderr.lower()

    def test_reject_stale_sha256(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        report["source_artifact_sha256"] = "0" * 64
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "SHA-256" in result.stderr

    def test_reject_passed_with_issues(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_VALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_passed_report(raw_path)
        report["issues"] = [
            {
                "code": "TEST",
                "item_id": "kw-1",
                "field": "confidence",
                "message": "test issue",
                "evidence": "test",
            }
        ]
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "passed report must contain no issues" in result.stderr

    def test_reject_failed_without_issues(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        raw_data = json.loads(FIXTURE_INVALID_RAW.read_text(encoding="utf-8"))
        raw_path = job_dir / "keywords_raw.json"
        raw_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        report = _make_failed_report(raw_path)
        report["issues"] = []
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "failed report must contain at least one issue" in result.stderr
