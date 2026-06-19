import json
from pathlib import Path

import yaml

from server.app.executors.config import load_executor_definitions
from server.app.jobs.queries import JobQueries
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.question_comprehension_info import (
    assemble_comprehension_info,
    clean_and_parse,
)


def test_clean_and_parse_preserves_cms_fingerprint(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    (artifact_dir / "questions.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "title": "CMS 题目一",
                        "normalized": {
                            "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                            "options": [{"label": "A", "text": "4场"}],
                            "answer": "A",
                            "analysis": "",
                            "fingerprint": "cms-fp-001",
                        },
                        "cms_payload": {"data": {"fingerprint": "cms-fp-001"}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    clean_and_parse(job, artifact_dir, {})

    parsed = json.loads((artifact_dir / "questions_parsed.json").read_text(encoding="utf-8"))
    assert parsed["questions"][0]["fingerprint"] == "cms-fp-001"
    assert parsed["questions"][0]["fingerprint_source"] == "cms"
    assert parsed["questions"][0]["fingerprint_missing"] is False


def test_clean_and_parse_marks_missing_fingerprint_without_hashing(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    (artifact_dir / "questions.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "title": "CMS 题目一",
                        "normalized": {
                            "stem": "1 + 1 = ?",
                            "options": [{"label": "A", "text": "2"}],
                            "answer": "A",
                            "analysis": "",
                        },
                        "cms_payload": {"data": {"question_id": "Q100"}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    clean_and_parse(job, artifact_dir, {})

    parsed = json.loads((artifact_dir / "questions_parsed.json").read_text(encoding="utf-8"))
    question = parsed["questions"][0]
    assert question["fingerprint"] is None
    assert question["fingerprint_source"] == "missing"
    assert question["fingerprint_missing"] is True
    assert "sha256" not in json.dumps(question, ensure_ascii=False).lower()


def test_assemble_comprehension_info_writes_package_artifacts(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["assemble_comprehension_info"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    (artifact_dir / "questions_parsed.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                        "options": [{"label": "A", "text": "4场"}],
                        "answer": "A",
                        "analysis": "",
                        "fingerprint": None,
                        "fingerprint_source": "missing",
                        "fingerprint_missing": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact_dir / "key_info_reviewed.json").write_text(
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
                            "options": [{"label": "A", "text": "14场", "is_correct": True}],
                        },
                        "question_comprehension_abilities": ["information_locating"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact_dir / "possible_errors_reviewed.json").write_text(
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
    (artifact_dir / "comprehension_difficulty.json").write_text(
        json.dumps({"question_id": "Q100", "comprehension_difficulty": 65}),
        encoding="utf-8",
    )

    assemble_comprehension_info(job, artifact_dir, {})

    payload = json.loads((artifact_dir / "comprehension_info.json").read_text(encoding="utf-8"))
    assert payload["question_id"] == "Q100"
    assert payload["fingerprint"] is None
    assert payload["fingerprint_missing"] is True
    assert payload["comprehension_data"]["fingerprint"] is None
    assert payload["comprehension_data"]["comprehension_difficulty"] == 65
    assert payload["comprehension_data"]["key_info_list"][0]["key_info_id"] == "ki_001"
    assert payload["comprehension_data"]["possible_error_list"][0]["error_id"] == "pe_001"

    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["question_id"] == "Q100"
    assert manifest["fingerprint_missing"] is True
    assert manifest["artifacts"]["comprehension_info.json"]["present"] is True


def test_local_executor_config_binds_question_comprehension_info_handlers():
    raw = yaml.safe_load(Path("config/workflow.yaml").read_text(encoding="utf-8"))
    config = load_executor_definitions(raw["executors"])
    local = config["local-default"]
    assert local.kind == "local"

    for capability in ("fetch_questions", "clean_and_parse", "assemble_comprehension_info"):
        handler = local.capabilities[capability].handler
        assert handler.startswith("question_comprehension_info."), (
            f"capability {capability!r} must bind to question_comprehension_info, got {handler!r}"
        )
