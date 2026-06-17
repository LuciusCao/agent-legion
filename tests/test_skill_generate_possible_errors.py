import json
import subprocess
from pathlib import Path

VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "server/app/pipelines/skills/question_comprehension_info/generate_possible_errors/scripts/validate_output.py"
)


def _write_valid_inputs(job_dir: Path):
    (job_dir / "questions_parsed.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                        "options": [],
                        "fingerprint": None,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (job_dir / "key_info_reviewed.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {"text": "胜5场", "position": {"start": 12, "end": 15}},
                        "question": {
                            "text": "小明胜了多少场？",
                            "options": [
                                {"label": "A", "text": "5场", "is_correct": True},
                                {"label": "B", "text": "4场", "is_correct": False},
                            ],
                        },
                        "question_comprehension_abilities": ["information_locating"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_generate_possible_errors_validator_accepts_valid_artifact(tmp_path):
    _write_valid_inputs(tmp_path)
    (tmp_path / "possible_errors_raw.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "possible_error_list": [
                    {
                        "error_id": "pe_001",
                        "error_type": "question_comprehension",
                        "error_answer": "5",
                        "error_description": "学生只看了胜5场，直接选5。",
                        "related_key_info_ids": ["ki_001"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "possible_errors_report.json").write_text(
        json.dumps({"question_id": "Q100", "warnings": []}),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_generate_possible_errors_validator_rejects_missing_key_info_reference(tmp_path):
    _write_valid_inputs(tmp_path)
    (tmp_path / "possible_errors_raw.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "possible_error_list": [
                    {
                        "error_id": "pe_001",
                        "error_type": "question_comprehension",
                        "error_answer": "5",
                        "error_description": "学生只看了胜5场，直接选5。",
                        "related_key_info_ids": ["ki_missing"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "possible_errors_report.json").write_text(
        json.dumps({"question_id": "Q100", "warnings": []}),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ki_missing" in result.stderr
