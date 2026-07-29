from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.app.executors.cancellation import CancellationToken, CancelledError
from workflow_nodes import question_intake
from workflow_nodes.comprehension_assemble import run as assemble_comprehension_info
from workflow_nodes.question_clean_parse import run as clean_and_parse


def _write_questions_json(artifact_dir: Path, questions: list) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "questions.json").write_text(
        __import__("json").dumps({"questions": questions}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_fetch_questions_without_cms(tmp_path):
    job = {"source_id": "q1", "title": "Title", "source_type": "question"}
    artifact_dir = tmp_path / "artifacts"
    question_intake.run(job, artifact_dir, {})

    out_path = artifact_dir / "questions.json"
    assert out_path.is_file()
    data = __import__("json").loads(out_path.read_text(encoding="utf-8"))
    assert data["questions"][0]["question_id"] == "q1"
    assert data["questions"][0]["cms_payload"] is None


def test_fetch_questions_with_cms(tmp_path):
    job = {"source_id": "q1", "title": "Title", "source_type": "question"}
    artifact_dir = tmp_path / "artifacts"
    detail = MagicMock()
    detail.question_id = "q1"
    detail.title = "CMS Title"
    detail.normalized = {"stem": "stem"}
    detail.payload = {"raw": "data"}

    settings_config = {"cms": {"base_url": "https://cms.example.com"}}
    node_config = {"api_url": "https://cms.example.com/question/detail"}

    with (
        patch("workflow_nodes.question_intake.get_token", return_value="token"),
        patch(
            "workflow_nodes.question_intake.fetch_question_detail",
            return_value=detail,
        ) as mock_fetch,
    ):
        question_intake.run(
            job,
            artifact_dir,
            {"settings_config": settings_config, "node_config": node_config},
        )

    mock_fetch.assert_called_once_with("q1", "https://cms.example.com/question/detail", "token")
    data = __import__("json").loads((artifact_dir / "questions.json").read_text(encoding="utf-8"))
    assert data["questions"][0]["title"] == "CMS Title"


def test_clean_and_parse_missing_file(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    job = {"source_id": "q1"}
    with pytest.raises(ValueError, match="questions.json not found"):
        clean_and_parse(job, artifact_dir)


def test_clean_and_parse_empty_questions(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(artifact_dir, [])
    with pytest.raises(ValueError, match="no questions"):
        clean_and_parse({"source_id": "q1"}, artifact_dir)


def test_clean_and_parse_invalid_record(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(artifact_dir, ["not-a-dict"])
    with pytest.raises(ValueError, match="Invalid question record"):
        clean_and_parse({"source_id": "q1"}, artifact_dir)


def test_clean_and_parse_missing_question_id(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(artifact_dir, [{"normalized": {}}])
    with pytest.raises(ValueError, match="Missing question_id"):
        clean_and_parse({"source_id": "q1"}, artifact_dir)


def test_clean_and_parse_normalizes_non_dict_normalized(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(
        artifact_dir,
        [
            {
                "question_id": "q1",
                "normalized": "not-a-dict",
            }
        ],
    )
    clean_and_parse({"source_id": "q1"}, artifact_dir)
    parsed = __import__("json").loads(
        (artifact_dir / "questions_parsed.json").read_text(encoding="utf-8")
    )
    assert parsed["questions"][0]["question_id"] == "q1"
    assert parsed["questions"][0]["stem"] == ""


def test_clean_and_parse_uses_cms_fingerprint(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(
        artifact_dir,
        [
            {
                "question_id": "q1",
                "normalized": {
                    "stem": "stem",
                    "options": ["A", "B"],
                    "fingerprint": "cms-fp",
                },
            }
        ],
    )
    clean_and_parse({"source_id": "q1"}, artifact_dir)
    parsed = __import__("json").loads(
        (artifact_dir / "questions_parsed.json").read_text(encoding="utf-8")
    )
    assert parsed["questions"][0]["fingerprint"] == "cms-fp"
    assert parsed["questions"][0]["fingerprint_source"] == "cms"
    assert parsed["questions"][0]["fingerprint_missing"] is False


def test_clean_and_parse_computes_md5_fingerprint(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(
        artifact_dir,
        [
            {
                "question_id": "q1",
                "normalized": {"stem": "stem", "options": ["A", "B"]},
            }
        ],
    )
    clean_and_parse({"source_id": "q1"}, artifact_dir)
    parsed = __import__("json").loads(
        (artifact_dir / "questions_parsed.json").read_text(encoding="utf-8")
    )
    assert parsed["questions"][0]["fingerprint"] is not None
    assert parsed["questions"][0]["fingerprint_source"] == "md5"
    assert parsed["questions"][0]["fingerprint_missing"] is False


def test_clean_and_parse_missing_fingerprint(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(
        artifact_dir,
        [
            {
                "question_id": "q1",
                "normalized": {},
            }
        ],
    )
    clean_and_parse({"source_id": "q1"}, artifact_dir)
    parsed = __import__("json").loads(
        (artifact_dir / "questions_parsed.json").read_text(encoding="utf-8")
    )
    assert parsed["questions"][0]["fingerprint"] is None
    assert parsed["questions"][0]["fingerprint_source"] == "missing"
    assert parsed["questions"][0]["fingerprint_missing"] is True


def test_clean_and_parse_respects_cancellation(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_questions_json(artifact_dir, [{"question_id": "q1", "normalized": {}}])
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancelledError):
        clean_and_parse({"source_id": "q1"}, artifact_dir, {"cancellation": token})


def _write_comprehension_inputs(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "questions_parsed_lean.json").write_text(
        __import__("json").dumps(
            {
                "questions": [
                    {"question_id": "q1", "fingerprint": "fp1", "fingerprint_source": "cms"}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact_dir / "key_info_reviewed.json").write_text(
        __import__("json").dumps(
            {
                "question_id": "q1",
                "key_info_list": [
                    {
                        "key_info_id": "ki_001",
                        "type": "given",
                        "content": {"text": "题干中的关键信息", "position": {"start": 0, "end": 5}},
                        "question": {
                            "text": "关键问题是什么？",
                            "options": [{"label": "A", "text": "正确选项", "is_correct": True}],
                        },
                        "question_comprehension_ability": "information_locating",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact_dir / "possible_errors_reviewed.json").write_text(
        __import__("json").dumps(
            {"question_id": "q1", "possible_error_list": []}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    (artifact_dir / "comprehension_difficulty.json").write_text(
        __import__("json").dumps(
            {"question_id": "q1", "comprehension_difficulty": 50}, ensure_ascii=False
        ),
        encoding="utf-8",
    )


def test_assemble_comprehension_info(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_comprehension_inputs(artifact_dir)
    job = {"source_id": "q1", "source_type": "question", "title": "Title", "workflow_key": "test"}
    assemble_comprehension_info(job, artifact_dir)

    payload = __import__("json").loads(
        (artifact_dir / "comprehension_info.json").read_text(encoding="utf-8")
    )
    assert payload["question_id"] == "q1"
    assert payload["fingerprint"] == "fp1"
    assert payload["fingerprint_source"] == "cms"

    manifest = __import__("json").loads(
        (artifact_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["question_id"] == "q1"
    assert manifest["artifacts"]["comprehension_info.json"]["present"] is True


def test_load_json_object_rejects_non_dict(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "bad.json").write_text("[1, 2, 3]", encoding="utf-8")
    from server.app.workflows.comprehension_common import _load_json_object

    with pytest.raises(ValueError, match="Invalid content"):
        _load_json_object(artifact_dir / "bad.json")


def _write_parsed_json(artifact_dir: Path, questions: list) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "questions_parsed.json").write_text(
        __import__("json").dumps({"questions": questions}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_single_parsed_question_rejects_multiple_questions(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    from server.app.workflows.comprehension_common import _single_parsed_question

    _write_parsed_json(artifact_dir, [{"question_id": "q1"}, {"question_id": "q2"}])
    with pytest.raises(ValueError, match="exactly one question"):
        _single_parsed_question(artifact_dir, "q1")


def test_single_parsed_question_rejects_non_dict_question(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    from server.app.workflows.comprehension_common import _single_parsed_question

    _write_parsed_json(artifact_dir, ["not-a-dict"])
    with pytest.raises(ValueError, match="invalid question"):
        _single_parsed_question(artifact_dir, "q1")


def test_assemble_comprehension_info_missing_input(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "questions_parsed.json").write_text(
        __import__("json").dumps(
            {"questions": [{"question_id": "q1", "fingerprint": "fp1"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = {"source_id": "q1"}
    with pytest.raises(ValueError, match="Missing input"):
        assemble_comprehension_info(job, artifact_dir)


def test_assemble_comprehension_info_input_question_id_mismatch(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_comprehension_inputs(artifact_dir)
    # Corrupt key_info_reviewed.json to have mismatched question_id
    (artifact_dir / "key_info_reviewed.json").write_text(
        __import__("json").dumps({"question_id": "q2", "key_info_list": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    job = {"source_id": "q1"}
    with pytest.raises(ValueError, match="question_id mismatch"):
        assemble_comprehension_info(job, artifact_dir)


def test_assemble_comprehension_info_question_id_mismatch(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_comprehension_inputs(artifact_dir)
    # Replace parsed lean question with mismatched id
    (artifact_dir / "questions_parsed_lean.json").write_text(
        __import__("json").dumps(
            {"questions": [{"question_id": "q2", "fingerprint": "fp1"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = {"source_id": "q1"}
    with pytest.raises(ValueError, match="Expected question_id"):
        assemble_comprehension_info(job, artifact_dir)


def test_assemble_comprehension_info_invalid_fingerprint_type(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_comprehension_inputs(artifact_dir)
    (artifact_dir / "questions_parsed_lean.json").write_text(
        __import__("json").dumps(
            {"questions": [{"question_id": "q1", "fingerprint": 123}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = {"source_id": "q1"}
    with pytest.raises(ValueError, match="fingerprint must be a string or null"):
        assemble_comprehension_info(job, artifact_dir)


def test_assemble_comprehension_info_respects_cancellation(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    _write_comprehension_inputs(artifact_dir)
    token = CancellationToken()
    token.cancel()
    job = {"source_id": "q1"}
    with pytest.raises(CancelledError):
        assemble_comprehension_info(job, artifact_dir, {"cancellation": token})
