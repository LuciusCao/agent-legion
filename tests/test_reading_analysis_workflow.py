import json
from pathlib import Path

import pytest

from server.app.jobs import JobQueries
from server.app.workflows.executor import LOCAL_HANDLERS
from server.app.workflows.reading_analysis import (
    clean_and_parse,
    fetch_questions,
    mark_question,
)


def test_fetch_questions_with_cms_writes_single_question(tmp_path, monkeypatch):
    from server.app.cms.question import CmsQuestionDetail

    monkeypatch.setattr(
        "server.app.workflows.reading_analysis.fetch_question_detail",
        lambda qid, url, token: CmsQuestionDetail(
            question_id="Q100",
            title="CMS 题目一",
            normalized={"stem": "1 + 1 = ?"},
            payload={"data": {"uuid": "Q100"}},
        ),
    )

    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "mark_question"],
    )
    artifact_dir = Path(job["storage_dir"])

    fetch_questions(
        job,
        artifact_dir,
        {
            "job_db": queries,
            "settings_config": {
                "cms": {"question_detail_url": "https://cms.example/detail", "env": "prod"}
            },
        },
    )

    questions = json.loads((artifact_dir / "questions.json").read_text())
    assert questions == {
        "questions": [
            {
                "question_id": "Q100",
                "title": "CMS 题目一",
                "normalized": {"stem": "1 + 1 = ?"},
                "cms_payload": {"data": {"uuid": "Q100"}},
            }
        ]
    }


def test_fetch_questions_without_cms_writes_base_payload(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "mark_question"],
    )
    artifact_dir = Path(job["storage_dir"])

    fetch_questions(
        job,
        artifact_dir,
        {
            "job_db": queries,
            "settings_config": {},
        },
    )

    questions = json.loads((artifact_dir / "questions.json").read_text())
    assert questions["questions"][0]["question_id"] == "Q100"
    assert questions["questions"][0]["title"] == "Question Q100"


def test_clean_and_parse_produces_normalized_question(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "mark_question"],
    )
    artifact_dir = Path(job["storage_dir"])
    (artifact_dir / "questions.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "title": "CMS 题目一",
                        "normalized": {
                            "stem": "1 + 1 = ?",
                            "options": [],
                            "answer": "2",
                            "analysis": "",
                        },
                        "cms_payload": {"data": {"uuid": "Q100"}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    clean_and_parse(job, artifact_dir, {})

    parsed = json.loads((artifact_dir / "questions_parsed.json").read_text())
    assert parsed == {
        "questions": [
            {
                "question_id": "Q100",
                "stem": "1 + 1 = ?",
                "options": [],
                "answer": "2",
                "analysis": "",
            }
        ]
    }


def test_clean_and_parse_fails_when_question_id_missing(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "mark_question"],
    )
    artifact_dir = Path(job["storage_dir"])
    (artifact_dir / "questions.json").write_text(
        json.dumps({"questions": [{"title": "no id"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="question_id"):
        clean_and_parse(job, artifact_dir, {})


def test_mark_question_joins_reviewed_artifacts(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "mark_question"],
    )
    artifact_dir = Path(job["storage_dir"])
    (artifact_dir / "keywords_reviewed.json").write_text(
        json.dumps({"question_id": "Q100", "keywords": ["加法"]}),
        encoding="utf-8",
    )
    (artifact_dir / "difficulty_reviewed.json").write_text(
        json.dumps({"question_id": "Q100", "reading_difficulty": 1}),
        encoding="utf-8",
    )
    (artifact_dir / "distractors_reviewed.json").write_text(
        json.dumps({"question_id": "Q100", "distractors": []}),
        encoding="utf-8",
    )

    mark_question(job, artifact_dir, {})

    marks = json.loads((artifact_dir / "question_marks.json").read_text())
    assert marks == {
        "questions": [
            {
                "question_id": "Q100",
                "keywords": ["加法"],
                "reading_difficulty": 1,
                "distractors": [],
            }
        ]
    }


def test_mark_question_fails_when_question_id_mismatch(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "mark_question"],
    )
    artifact_dir = Path(job["storage_dir"])
    (artifact_dir / "keywords_reviewed.json").write_text(
        json.dumps({"question_id": "Q100", "keywords": ["加法"]}),
        encoding="utf-8",
    )
    (artifact_dir / "difficulty_reviewed.json").write_text(
        json.dumps({"question_id": "Q200", "reading_difficulty": 1}),
        encoding="utf-8",
    )
    (artifact_dir / "distractors_reviewed.json").write_text(
        json.dumps({"question_id": "Q100", "distractors": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="question_id"):
        mark_question(job, artifact_dir, {})


def test_local_handlers_registered():
    assert "reading_analysis" in LOCAL_HANDLERS
    assert LOCAL_HANDLERS["reading_analysis"]["fetch_questions"] == fetch_questions
    assert LOCAL_HANDLERS["reading_analysis"]["clean_and_parse"] == clean_and_parse
    assert LOCAL_HANDLERS["reading_analysis"]["mark_question"] == mark_question
