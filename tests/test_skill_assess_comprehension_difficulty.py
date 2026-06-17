import json
import subprocess
from pathlib import Path

VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "server/app/pipelines/skills/question_comprehension_info/assess_comprehension_difficulty/scripts/validate_output.py"
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
                        "content": {"text": "14场", "position": {"start": 6, "end": 9}},
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
                            "derived_text": "平局场数 = 14 - 5 - 5 = 4",
                            "position": {"start": 0, "end": 0},
                            "derivation": "总场次减去胜负场",
                        },
                        "question": {
                            "text": "小明平了多少场？",
                            "options": [
                                {"label": "A", "text": "4场", "is_correct": True},
                                {"label": "B", "text": "5场", "is_correct": False},
                            ],
                        },
                        "question_comprehension_abilities": ["inference"],
                    },
                    {
                        "key_info_id": "ki_003",
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
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (job_dir / "possible_errors_reviewed.json").write_text(
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
                    },
                    {
                        "error_id": "pe_002",
                        "error_type": "question_comprehension",
                        "error_answer": "10",
                        "error_description": "学生用14-5得到负和平局总数，但误以为是负场数。",
                        "related_key_info_ids": ["ki_001", "ki_002"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_valid_outputs(job_dir: Path):
    (job_dir / "comprehension_difficulty.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "comprehension_difficulty": 65,
                "signals": {
                    "key_info_count": 3,
                    "hidden_info_count": 1,
                    "possible_error_count": 2,
                    "ability_count": 2,
                },
                "evidence": [
                    "包含一个隐藏信息推导",
                    "存在两个由审题遗漏导致的错误答案",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (job_dir / "comprehension_difficulty_report.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "warnings": [],
                "method": "基于关键信息数量、隐藏信息数量、可能错误数量和涉及能力数量综合评估",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_assess_comprehension_difficulty_validator_accepts_valid_artifact(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_assess_comprehension_difficulty_validator_rejects_too_low_score(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["comprehension_difficulty"] = 0
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "comprehension_difficulty must be in range 1..99" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_too_high_score(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["comprehension_difficulty"] = 100
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "comprehension_difficulty must be in range 1..99" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_missing_evidence(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    del data["evidence"]
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "evidence must be a non-empty list of strings" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_empty_evidence(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["evidence"] = []
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "evidence must be a non-empty list of strings" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_mismatched_question_id(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["question_id"] = "Q999"
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "question_id mismatch" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_bad_key_info_count(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["signals"]["key_info_count"] = 5
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "key_info_count" in result.stderr
    assert "does not match" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_bad_hidden_info_count(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["signals"]["hidden_info_count"] = 2
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "hidden_info_count" in result.stderr
    assert "does not match" in result.stderr


def test_assess_comprehension_difficulty_validator_rejects_bad_possible_error_count(tmp_path):
    _write_valid_inputs(tmp_path)
    _write_valid_outputs(tmp_path)
    data = json.loads((tmp_path / "comprehension_difficulty.json").read_text(encoding="utf-8"))
    data["signals"]["possible_error_count"] = 5
    (tmp_path / "comprehension_difficulty.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        ["python", str(VALIDATOR), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "possible_error_count" in result.stderr
    assert "does not match" in result.stderr
