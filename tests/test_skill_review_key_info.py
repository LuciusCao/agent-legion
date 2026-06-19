import json
import subprocess
from pathlib import Path

VALIDATOR = (
    Path.home()
    / ".agents/skills/agent-legion/question_comprehension_info/review_key_info/scripts/validate_output.py"
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
    (job_dir / "key_info_raw.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {"text": "14场", "position": {"start": 6, "end": 9}},
                        "question": {
                            "text": "小明一共参加了多少场比赛？",
                            "options": [
                                {"label": "A", "text": "14场", "is_correct": True},
                                {"label": "B", "text": "5场", "is_correct": False},
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


def test_review_key_info_validator_accepts_valid_artifact(tmp_path):
    _write_valid_inputs(tmp_path)
    (tmp_path / "key_info_reviewed.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {"text": "14场", "position": {"start": 6, "end": 9}},
                        "question": {
                            "text": "小明一共参加了多少场比赛？",
                            "options": [
                                {"label": "A", "text": "14场", "is_correct": True},
                                {"label": "B", "text": "5场", "is_correct": False},
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
    (tmp_path / "key_info_review_report.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "approved_count": 1,
                "rejected_count": 0,
                "warnings": [],
                "decisions": [{"key_info_id": "ki_001", "decision": "approved", "reason": "valid"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_review_key_info_validator_rejects_count_mismatch(tmp_path):
    _write_valid_inputs(tmp_path)
    (tmp_path / "key_info_reviewed.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {"text": "14场", "position": {"start": 6, "end": 9}},
                        "question": {
                            "text": "小明一共参加了多少场比赛？",
                            "options": [
                                {"label": "A", "text": "14场", "is_correct": True},
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
    (tmp_path / "key_info_review_report.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "approved_count": 0,
                "rejected_count": 0,
                "warnings": [],
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "approved_count + rejected_count (0) must equal len(key_info_list) (1)" in result.stderr
