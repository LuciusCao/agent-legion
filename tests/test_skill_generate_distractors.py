from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / (
    "server/app/workflows/skills/reading_analysis/generate_distractors/scripts/validate_output.py"
)
FIXTURE_QUESTIONS = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/reading_analysis/questions_parsed.json"
)
FIXTURE_KEYWORDS = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/reading_analysis/eval/generate_distractors/keywords_reviewed.json"
)


DUMMY_KEYWORDS: dict = {
    "question_id": "Q100",
    "keywords": [
        {
            "id": "kw-1",
            "source_text": "单程 1400km",
            "normalized_text": "单程距离",
            "location": {"source": "stem", "option_key": None, "start": 11, "end": 20},
            "necessity": "决定路程计算的必要条件",
            "counterfactual": "删除距离后无法完成数值计算",
            "confidence": 0.96,
        }
    ],
}


def _run_validator(job_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(job_dir)],
        capture_output=True,
        text=True,
    )


def _make_valid_distractors_raw(question: dict) -> dict:
    stem = question["stem"]
    shanghai_start = stem.index("上海")
    shanghai_end = shanghai_start + len("上海")
    beijing_start = stem.index("北京")
    beijing_end = beijing_start + len("北京")
    return {
        "question_id": question["question_id"],
        "distractors": [
            {
                "id": "dist-1",
                "source_text": "上海",
                "normalized_text": "出发地名称",
                "location": {
                    "source": "stem",
                    "option_key": None,
                    "start": shanghai_start,
                    "end": shanghai_end,
                },
                "relevance": "属于行程情境信息",
                "non_necessity": "替换城市名称不影响距离计算",
                "counterfactual": "替换为其他城市后解题过程和答案不变",
                "confusion_strength": 72,
                "confidence": 0.94,
            },
            {
                "id": "dist-2",
                "source_text": "北京",
                "normalized_text": "目的地名称",
                "location": {
                    "source": "stem",
                    "option_key": None,
                    "start": beijing_start,
                    "end": beijing_end,
                },
                "relevance": "属于行程情境信息",
                "non_necessity": "替换城市名称不影响距离计算",
                "counterfactual": "替换为其他城市后解题过程和答案不变",
                "confusion_strength": 70,
                "confidence": 0.92,
            },
        ],
    }


def _make_valid_report(question: dict) -> dict:
    return {
        "question_id": question["question_id"],
        "candidate_count": 2,
        "method": "counterfactual_deletion",
        "keyword_conflicts_excluded": ["单程 1400km"],
        "warnings": [],
    }


@pytest.fixture
def question() -> dict:
    data = json.loads(FIXTURE_QUESTIONS.read_text(encoding="utf-8"))
    return data["questions"][0]


class TestGenerateDistractorsValidator:
    def test_valid_artifacts_pass(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(_make_valid_distractors_raw(question), ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_missing_distractor_field(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        del raw["distractors"][0]["relevance"]
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "relevance" in result.stderr

    def test_duplicate_ids_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][1]["id"] = raw["distractors"][0]["id"]
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "duplicate id" in result.stderr

    def test_duplicate_source_text_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][1]["source_text"] = raw["distractors"][0]["source_text"]
        # Adjust location to match the duplicated text so location check passes
        raw["distractors"][1]["location"] = raw["distractors"][0]["location"].copy()
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "duplicate" in result.stderr.lower() and "source_text" in result.stderr.lower()

    def test_analysis_source_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["location"] = {
            "source": "analysis",
            "option_key": None,
            "start": 0,
            "end": 3,
        }
        raw["distractors"][0]["source_text"] = "往返距离"
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "stem or option" in result.stderr

    def test_location_mismatch_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["location"]["start"] = 0
        raw["distractors"][0]["location"]["end"] = 2
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "source_text mismatch" in result.stderr

    def test_empty_relevance_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["relevance"] = ""
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "relevance" in result.stderr

    def test_empty_non_necessity_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["non_necessity"] = "   "
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "non_necessity" in result.stderr

    def test_empty_counterfactual_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["counterfactual"] = ""
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "counterfactual" in result.stderr

    def test_confidence_out_of_range_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["confidence"] = 1.5
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "confidence" in result.stderr

    def test_confusion_strength_out_of_range_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["confusion_strength"] = 0
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "confusion_strength" in result.stderr

    def test_keyword_overlap_by_source_text_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["source_text"] = "单程 1400km"
        raw["distractors"][0]["location"] = {
            "source": "stem",
            "option_key": None,
            "start": 11,
            "end": 20,
        }
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "keyword" in result.stderr.lower() and "overlap" in result.stderr.lower()

    def test_keyword_overlap_by_source_range_rejected(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        raw = _make_valid_distractors_raw(question)
        raw["distractors"][0]["source_text"] = "1400"
        raw["distractors"][0]["location"] = {
            "source": "stem",
            "option_key": None,
            "start": 14,
            "end": 18,
        }
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(_make_valid_report(question), ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "keyword" in result.stderr.lower() and "overlap" in result.stderr.lower()

    def test_report_question_id_mismatch(self, tmp_path: Path, question: dict) -> None:
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        (job_dir / "questions_parsed.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(_make_valid_distractors_raw(question), ensure_ascii=False), encoding="utf-8"
        )
        report = _make_valid_report(question)
        report["question_id"] = "Q999"
        (job_dir / "distractors_report.json").write_text(
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
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(DUMMY_KEYWORDS, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(_make_valid_distractors_raw(question), ensure_ascii=False), encoding="utf-8"
        )
        report = _make_valid_report(question)
        del report["candidate_count"]
        (job_dir / "distractors_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )

        result = _run_validator(job_dir)
        assert result.returncode == 1
        assert "candidate_count" in result.stderr
