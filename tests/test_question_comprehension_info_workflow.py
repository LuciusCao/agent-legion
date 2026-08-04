import json
import sys
from pathlib import Path

import pytest
import yaml

from server.app.executors import registration as _registration  # noqa: F401  # 触发内建 kind 注册
from server.app.executors.definitions import load_executor_definitions
from server.app.jobs.queries import JobQueries
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.question_comprehension_info import (
    assemble_comprehension_info,
    classify_comprehension_eligibility,
    clean_and_parse,
    finalize_non_uploadable,
)
from tests.postgres_support import TEST_DATABASE_URL

_TOOL_DIR = Path(__file__).parents[1] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader.schemas import registry  # noqa: E402


def test_clean_and_parse_preserves_cms_fingerprint(tmp_path):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
        workspace_id=workspace["id"],
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


def test_clean_and_parse_uses_md5_fallback_when_cms_fingerprint_missing(tmp_path):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
        workspace_id=workspace["id"],
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
    assert question["fingerprint"] == "597781f6fc22c1444e1f7c066faefd52"
    assert question["fingerprint_source"] == "md5"
    assert question["fingerprint_missing"] is False


def test_assemble_comprehension_info_writes_package_artifacts(tmp_path):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["assemble_comprehension_info"],
        workspace_id=workspace["id"],
        workflow_revision_id="test_ws:question_comprehension_info:v7",
        workflow_version=7,
        workflow_definition_hash="hash-v7",
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    (artifact_dir / "questions_parsed_lean.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                        "options": [{"label": "A", "text": "4场"}],
                        "answer": "A",
                        "analysis": [],
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
                            "text": "小明参加了多少场象棋比赛？",
                            "options": [{"label": "A", "text": "14场", "is_correct": True}],
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
        json.dumps(
            {
                "question_id": "Q100",
                "possible_error_list": [
                    {
                        "error_id": "pe_001",
                        "error_type": "question_comprehension",
                        "position": 1,
                        "error_answer": ["5"],
                        "error_description": "学生只看了胜5场，直接选5。",
                        "cognitive_basis": "学生只关注部分条件，忽略总数。",
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
    assert payload["schema_version"] == "v1"
    assert payload["comprehension_data"]["fingerprint"] is None
    assert payload["comprehension_data"]["comprehension_difficulty"] == 65
    assert payload["comprehension_data"]["key_info_list"][0]["key_info_id"] == "ki_001"
    assert payload["comprehension_data"]["possible_error_list"][0]["error_id"] == "pe_001"

    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["question_id"] == "Q100"
    assert manifest["workflow"] == {
        "key": "question_comprehension_info",
        "version": 7,
        "revision_id": "test_ws:question_comprehension_info:v7",
        "definition_hash": "hash-v7",
    }
    assert manifest["fingerprint_missing"] is True
    assert manifest["artifacts"]["comprehension_info.json"]["present"] is True


def test_assemble_comprehension_info_records_skill_versions(tmp_path):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=[
            "fetch_questions",
            "clean_and_parse",
            "generate_key_info",
            "assemble_comprehension_info",
        ],
        workspace_id=workspace["id"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    (artifact_dir / "questions_parsed_lean.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                        "options": [{"label": "A", "text": "4场"}],
                        "answer": "A",
                        "analysis": [],
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
                            "text": "小明参加了多少场象棋比赛？",
                            "options": [{"label": "A", "text": "14场", "is_correct": True}],
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
        json.dumps(
            {
                "question_id": "Q100",
                "possible_error_list": [
                    {
                        "error_id": "pe_001",
                        "error_type": "question_comprehension",
                        "position": 1,
                        "error_answer": ["5"],
                        "error_description": "学生只看了胜5场，直接选5。",
                        "cognitive_basis": "学生只关注部分条件，忽略总数。",
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

    run = queries.start_node_run(
        job["id"], "generate_key_info", ["pi"], "", skill_version="v1.2.2@abc123"
    )
    queries.finish_node_run(run["id"], "completed", 0, "")
    empty_run = queries.start_node_run(
        job["id"], "clean_and_parse", ["local"], "", skill_version=""
    )
    queries.finish_node_run(empty_run["id"], "completed", 0, "")

    assemble_comprehension_info(job, artifact_dir, {"job_db": queries})

    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skill_versions"] == {
        "generate_key_info": "v1.2.2@abc123",
    }
    assert all(version for version in manifest["skill_versions"].values())


def test_clean_and_parse_md5_is_deterministic(tmp_path):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
        workspace_id=workspace["id"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")

    def _write_questions(stem, options):
        (artifact_dir / "questions.json").write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "question_id": "Q100",
                            "title": "CMS 题目一",
                            "normalized": {
                                "stem": stem,
                                "options": options,
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

    _write_questions(
        "  小明参加了14场象棋比赛，胜5场，负5场，其余为平局。  ",
        [{"label": "A", "text": "  4场  "}, {"label": "B", "text": "5场"}],
    )
    clean_and_parse(job, artifact_dir, {})
    parsed = json.loads((artifact_dir / "questions_parsed.json").read_text(encoding="utf-8"))
    first_fingerprint = parsed["questions"][0]["fingerprint"]
    assert first_fingerprint is not None

    _write_questions(
        "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
        [{"label": "B", "text": "5场"}, {"label": "A", "text": "4场"}],
    )
    clean_and_parse(job, artifact_dir, {})
    parsed = json.loads((artifact_dir / "questions_parsed.json").read_text(encoding="utf-8"))
    second_fingerprint = parsed["questions"][0]["fingerprint"]

    assert first_fingerprint == second_fingerprint


def test_clean_and_parse_missing_fingerprint_when_no_content(tmp_path):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["fetch_questions", "clean_and_parse", "assemble_comprehension_info"],
        workspace_id=workspace["id"],
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
                            "stem": "   ",
                            "options": [],
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


def test_local_executor_config_binds_question_comprehension_info_handlers():
    raw = yaml.safe_load(Path("config/workflow.yaml").read_text(encoding="utf-8"))
    config = load_executor_definitions(raw["executors"])
    local = config["local-default"]
    assert local.kind == "local"

    for capability in (
        "fetch_questions",
        "clean_and_parse",
        "classify_comprehension_eligibility",
        "finalize_non_uploadable",
        "assemble_comprehension_info",
    ):
        handler = local.capabilities[capability].handler
        assert handler.startswith("question_comprehension_info."), (
            f"capability {capability!r} must bind to question_comprehension_info, got {handler!r}"
        )


def test_classify_comprehension_eligibility_marks_pure_calculation_non_uploadable(tmp_path):
    job = {"source_id": "Q200"}
    tmp_path.joinpath("questions_parsed.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q200",
                        "stem": "计算：125 + 76 = ?",
                        "options": [{"label": "A", "text": "201"}],
                        "answer": "A",
                        "analysis": "",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    classify_comprehension_eligibility(job, tmp_path, {})

    payload = json.loads(
        tmp_path.joinpath("comprehension_eligibility.json").read_text(encoding="utf-8")
    )
    assert payload["question_id"] == "Q200"
    assert payload["eligible"] is False
    assert payload["reason_code"] == "pure_calculation"


def test_finalize_non_uploadable_writes_non_uploadable_manifest(tmp_path):
    job = {
        "id": "job1",
        "source_id": "Q200",
        "workflow_key": "question_comprehension_info",
        "source_type": "question",
        "title": "Question Q200",
    }
    tmp_path.joinpath("questions_parsed.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q200",
                        "fingerprint": "fp-1",
                        "fingerprint_source": "cms",
                        "fingerprint_missing": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    tmp_path.joinpath("comprehension_eligibility.json").write_text(
        json.dumps(
            {
                "question_id": "Q200",
                "eligible": False,
                "reason_code": "pure_calculation",
                "reason": "题目主要考查直接计算，没有独立审题信息。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    finalize_non_uploadable(job, tmp_path, {})

    manifest = json.loads(tmp_path.joinpath("manifest.json").read_text(encoding="utf-8"))
    assert manifest["question_id"] == "Q200"
    assert manifest["uploadable"] is False
    assert manifest["outcome"] == "non_uploadable"
    assert manifest["skip_reason_code"] == "pure_calculation"
    assert not tmp_path.joinpath("comprehension_info.json").exists()


def test_assemble_comprehension_info_passes_uploader_v1_schema_validation(tmp_path):
    """Regression: assemble must include the Socratic question field required by v1."""
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["assemble_comprehension_info"],
        workspace_id=workspace["id"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    (artifact_dir / "questions_parsed_lean.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                        "options": [{"label": "A", "text": "4场"}],
                        "answer": "A",
                        "analysis": [],
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
                            "text": "小明参加了多少场象棋比赛？",
                            "options": [{"label": "A", "text": "14场", "is_correct": True}],
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
        json.dumps(
            {
                "question_id": "Q100",
                "possible_error_list": [
                    {
                        "error_id": "pe_001",
                        "error_type": "question_comprehension",
                        "position": 1,
                        "error_answer": ["5"],
                        "error_description": "学生只看了胜5场，直接选5。",
                        "cognitive_basis": "学生只关注部分条件，忽略总数。",
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
    validated = registry.validate("v1", payload["comprehension_data"])
    assert validated.fingerprint is None
    assert validated.comprehension_difficulty == 65
    assert len(validated.key_info_list) == 1
    assert validated.key_info_list[0].question.text == "小明参加了多少场象棋比赛？"


def _write_assemble_inputs(
    artifact_dir: Path,
    *,
    key_info_extra: dict | None = None,
    possible_error_extra: dict | None = None,
) -> None:
    (artifact_dir / "questions_parsed_lean.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q100",
                        "stem": "小明参加了14场象棋比赛，胜5场，负5场，其余为平局。",
                        "options": [{"label": "A", "text": "4场"}],
                        "answer": "A",
                        "analysis": [],
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
    key_info_item = {
        "key_info_id": "ki_001",
        "type": "given",
        "content": {"text": "14场", "position": {"start": 6, "end": 9}},
        "question": {
            "text": "小明参加了多少场象棋比赛？",
            "options": [{"label": "A", "text": "14场", "is_correct": True}],
        },
        "question_comprehension_ability": "information_locating",
    }
    if key_info_extra:
        key_info_item.update(key_info_extra)
    (artifact_dir / "key_info_reviewed.json").write_text(
        json.dumps(
            {"question_id": "Q100", "key_info_list": [key_info_item]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    possible_error_item = {
        "error_id": "pe_001",
        "error_type": "question_comprehension",
        "position": 1,
        "error_answer": ["5"],
        "error_description": "学生只看了胜5场，直接选5。",
        "cognitive_basis": "学生只关注部分条件，忽略总数。",
        "related_key_info_ids": ["ki_001"],
    }
    if possible_error_extra:
        possible_error_item.update(possible_error_extra)
    (artifact_dir / "possible_errors_reviewed.json").write_text(
        json.dumps(
            {"question_id": "Q100", "possible_error_list": [possible_error_item]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact_dir / "comprehension_difficulty.json").write_text(
        json.dumps({"question_id": "Q100", "comprehension_difficulty": 65}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "key_info_extra,possible_error_extra,match",
    [
        ({"reason": "valid hidden item"}, None, "key_info_list\\[0\\].*reason"),
        (None, {"decision": "approved"}, "possible_error_list\\[0\\].*decision"),
        (
            None,
            None,
            None,
        ),
    ],
)
def test_assemble_comprehension_info_rejects_extra_fields(
    tmp_path, key_info_extra, possible_error_extra, match
):
    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="batch1",
        title="Question Q100",
        node_keys=["assemble_comprehension_info"],
        workspace_id=workspace["id"],
    )
    artifact_dir = resolve_job_dir(job, tmp_path / "jobs")
    _write_assemble_inputs(
        artifact_dir,
        key_info_extra=key_info_extra,
        possible_error_extra=possible_error_extra,
    )

    if match is None:
        assemble_comprehension_info(job, artifact_dir, {})
        payload = json.loads((artifact_dir / "comprehension_info.json").read_text(encoding="utf-8"))
        registry.validate("v1", payload["comprehension_data"])
        return

    with pytest.raises(ValueError, match=match):
        assemble_comprehension_info(job, artifact_dir, {})
    assert not (artifact_dir / "comprehension_info.json").exists()
