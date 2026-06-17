import json
import subprocess
from pathlib import Path

VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "server/app/pipelines/skills/question_comprehension_info/generate_key_info/scripts/validate_output.py"
)


def test_generate_key_info_validator_accepts_valid_artifact(tmp_path):
    (tmp_path / "questions_parsed.json").write_text(
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
    (tmp_path / "key_info_raw.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {
                            "text": "14场",
                            "position": {"start": 6, "end": 9},
                        },
                        "question": {
                            "text": "小明一共参加了多少场比赛？",
                            "options": [
                                {"label": "A", "text": "14场", "is_correct": True},
                                {"label": "B", "text": "5场", "is_correct": False},
                            ],
                        },
                        "question_comprehension_abilities": ["information_locating"],
                    },
                    {
                        "key_info_id": "ki_002",
                        "type": "hidden",
                        "content": {
                            "derived_text": "平局场数",
                            "position": {"start": 24, "end": 27},
                            "derivation": "其余为平局",
                        },
                        "question": {
                            "text": "小明有多少场平局？",
                            "options": [
                                {"label": "A", "text": "4场", "is_correct": True},
                                {"label": "B", "text": "5场", "is_correct": False},
                            ],
                        },
                        "question_comprehension_abilities": ["inference"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "key_info_report.json").write_text(
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


def test_generate_key_info_validator_rejects_unknown_ability(tmp_path):
    (tmp_path / "questions_parsed.json").write_text(
        json.dumps({"questions": [{"question_id": "Q100", "stem": "abc"}]}),
        encoding="utf-8",
    )
    (tmp_path / "key_info_raw.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {"text": "abc", "position": {"start": 0, "end": 3}},
                        "question": {
                            "text": "文本是什么？",
                            "options": [{"label": "A", "text": "abc", "is_correct": True}],
                        },
                        "question_comprehension_abilities": ["made_up_ability"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "key_info_report.json").write_text(
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
    assert "made_up_ability" in result.stderr
