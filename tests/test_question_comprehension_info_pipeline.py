import json
from pathlib import Path

from server.app.jobs.queries import JobQueries
from server.app.pipelines.question_comprehension_info import clean_and_parse


def test_clean_and_parse_preserves_cms_fingerprint(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    job = queries.create_job(
        pipeline_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
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
        pipeline_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
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
