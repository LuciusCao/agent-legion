from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.app.workflows.skills.question_comprehension_info._shared.validation import (
    ContractError,
    normalize_key_info_positions,
    plain_text_from_html,
    validate_key_info_payload,
)

NORMALIZE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "server"
    / "app"
    / "workflows"
    / "skills"
    / "question_comprehension_info"
    / "generate_key_info"
    / "scripts"
    / "normalize_positions.py"
)


def _make_question(stem: str) -> dict:
    return {
        "question_id": "q1",
        "stem": stem,
        "options": [],
        "answer": [],
        "analysis": [],
    }


def _make_payload(text: str, start: int, end: int) -> dict:
    return {
        "question_id": "q1",
        "key_info_list": [
            {
                "key_info_id": "ki_1",
                "type": "given",
                "content": {
                    "text": text,
                    "position": {"start": start, "end": end},
                },
                "question": {
                    "text": "question",
                    "options": [
                        {"label": "A", "text": "opt", "is_correct": True},
                    ],
                },
                "question_comprehension_abilities": ["information_locating"],
            }
        ],
    }


def test_plain_text_from_html_strips_tags_and_entities():
    assert plain_text_from_html("<p>A &amp; B<br/>C</p><script>x</script>") == "A & B\nC"
    assert plain_text_from_html("plain text") == "plain text"
    assert plain_text_from_html(None) == ""


def test_validate_key_info_payload_accepts_plain_text_positions():
    question = _make_question("<p>每月可修276千米，修了17月。</p>")
    payload = _make_payload("每月可修276千米", 0, 9)
    validate_key_info_payload(payload, question, {"information_locating"})


def test_validate_key_info_payload_rejects_html_positions():
    question = _make_question("<p>每月可修276千米，修了17月。</p>")
    payload = _make_payload("每月可修276千米", 3, 12)
    with pytest.raises(ContractError) as exc_info:
        validate_key_info_payload(payload, question, {"information_locating"})
    assert "does not match stem slice [3:12]" in str(exc_info.value)


def test_validate_key_info_payload_rejects_position_out_of_range():
    question = _make_question("<p>每月可修276千米。</p>")
    payload = _make_payload("每月可修276千米", 0, 50)
    with pytest.raises(ContractError) as exc_info:
        validate_key_info_payload(payload, question, {"information_locating"})
    assert "exceeds plain stem length" in str(exc_info.value)


def test_normalize_key_info_positions_corrects_html_positions():
    stem = "<p>每月可修276千米，修了17月。</p>"
    payload = _make_payload("每月可修276千米", 3, 12)

    warnings = normalize_key_info_positions(payload, _make_question(stem))

    assert any("corrected from [3:12] to [0:9]" in w for w in warnings)
    position = payload["key_info_list"][0]["content"]["position"]
    assert position == {"start": 0, "end": 9}
    validate_key_info_payload(payload, _make_question(stem), {"information_locating"})


def test_normalize_key_info_positions_is_idempotent():
    stem = "<p>每月可修276千米，修了17月。</p>"
    payload = _make_payload("每月可修276千米", 0, 9)

    warnings = normalize_key_info_positions(payload, _make_question(stem))

    assert warnings == []
    position = payload["key_info_list"][0]["content"]["position"]
    assert position == {"start": 0, "end": 9}


def test_normalize_key_info_positions_warns_when_text_not_found():
    stem = "<p>每月可修276千米。</p>"
    payload = _make_payload("不存在", 0, 3)

    warnings = normalize_key_info_positions(payload, _make_question(stem))

    assert any("not found in plain stem" in w for w in warnings)
    position = payload["key_info_list"][0]["content"]["position"]
    assert position == {"start": 0, "end": 3}


def _write_job_dir(tmp_path: Path, stem: str, payload: dict) -> Path:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    questions = {"questions": [_make_question(stem)]}
    (job_dir / "questions_parsed.json").write_text(
        json.dumps(questions, ensure_ascii=False), encoding="utf-8"
    )
    (job_dir / "key_info_raw.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return job_dir


def test_normalize_positions_script_corrects_html_positions(tmp_path: Path):
    stem = "<p>每月可修276千米，修了17月。</p>"
    payload = _make_payload("每月可修276千米", 3, 12)
    job_dir = _write_job_dir(tmp_path, stem, payload)

    result = subprocess.run(
        ["python", str(NORMALIZE_SCRIPT), str(job_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "corrected from [3:12] to [0:9]" in result.stderr
    normalized = json.loads((job_dir / "key_info_raw.json").read_text(encoding="utf-8"))
    position = normalized["key_info_list"][0]["content"]["position"]
    assert position == {"start": 0, "end": 9}
