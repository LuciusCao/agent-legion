"""Unit tests for the demo workflow code nodes (example_intake/example_publish).

Pure node-level tests: build the job dict + runtime map the code executor
would pass, run the node against a tmp job_dir. No database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow_nodes import example_intake, example_publish

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]

_JOB = {
    "id": "job-demo-1",
    "source_id": "fraction-addition-subtraction",
    "title": "分数加减法",
    "workflow_key": "education_video_problems_generation",
    "workflow_version": 1,
    "workflow_revision_id": "rev-1",
    "workflow_definition_hash": "hash-1",
}


def _runtime(node_config: dict | None = None) -> dict:
    return {"node_key": "intake_knowledge_points", "node_config": node_config or {}}


def test_intake_reads_repo_examples_by_default(tmp_path: Path) -> None:
    """Default knowledge_dir resolves against the repo root examples tree."""
    example_intake.run(dict(_JOB), tmp_path, _runtime())

    payload = json.loads((tmp_path / "knowledge_point.json").read_text(encoding="utf-8"))
    point = payload["knowledge_point"]
    assert point["id"] == "fraction-addition-subtraction"
    assert point["title"] == "分数加减法"
    assert point["grade"] == "小学五年级"
    assert "通分" in point["summary"]
    assert len(point["common_mistakes"]) >= 3
    assert payload["source"]["file"] == "fraction-addition-subtraction.md"
    assert Path(payload["source"]["knowledge_dir"]).is_dir()


def test_intake_honors_knowledge_dir_override(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "points"
    knowledge_dir.mkdir()
    (knowledge_dir / "demo-point.md").write_text(
        "# 演示知识点\n"
        "\n"
        "- 适用年级：小学三年级\n"
        "- 学科：小学数学\n"
        "\n"
        "## 核心概念\n"
        "\n"
        "第一段概念。\n"
        "\n"
        "第二段概念。\n"
        "\n"
        "## 常见易错点\n"
        "\n"
        "- 易错点甲\n"
        "- 易错点乙\n",
        encoding="utf-8",
    )
    job = {**_JOB, "source_id": "demo-point"}
    runtime = _runtime({"knowledge_dir": str(knowledge_dir)})

    example_intake.run(job, tmp_path / "job", runtime)

    payload = json.loads((tmp_path / "job" / "knowledge_point.json").read_text(encoding="utf-8"))
    point = payload["knowledge_point"]
    assert point["title"] == "演示知识点"
    assert point["summary"] == "第一段概念。\n\n第二段概念。"
    assert point["common_mistakes"] == ["易错点甲", "易错点乙"]


def test_intake_missing_file_lists_available_ids(tmp_path: Path) -> None:
    job = {**_JOB, "source_id": "no-such-point"}
    with pytest.raises(RuntimeError, match="知识点文件不存在") as excinfo:
        example_intake.run(job, tmp_path, _runtime())
    # The error guides the user to the available intake values.
    assert "fraction-addition-subtraction" in str(excinfo.value)


def test_intake_rejects_malformed_markdown(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "points"
    knowledge_dir.mkdir()
    (knowledge_dir / "broken.md").write_text("# 只有标题\n", encoding="utf-8")
    job = {**_JOB, "source_id": "broken"}
    runtime = _runtime({"knowledge_dir": str(knowledge_dir)})
    with pytest.raises(ValueError, match="核心概念"):
        example_intake.run(job, tmp_path / "job", runtime)


def _write_publish_inputs(job_dir: Path) -> None:
    (job_dir / "knowledge_point.json").write_text(
        json.dumps(
            {
                "knowledge_point": {"id": "demo", "title": "演示知识点"},
                "source": {"file": "demo.md"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (job_dir / "script.md").write_text("## 开场导入\n……\n", encoding="utf-8")
    (job_dir / "script_review.json").write_text(
        json.dumps({"verdict": "pass", "summary": "ok"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (job_dir / "exercises.json").write_text(
        json.dumps(
            {
                "knowledge_point_id": "demo",
                "exercises": [
                    {
                        "id": f"q{i}",
                        "difficulty": "easy",
                        "stem": "s",
                        "answer": "a",
                        "analysis": "x",
                    }
                    for i in range(1, 6)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (job_dir / "exercises_review.json").write_text(
        json.dumps({"verdict": "pass", "exercise_reviews": [], "summary": "ok"}),
        encoding="utf-8",
    )


def test_publish_aggregates_upstream_artifacts(tmp_path: Path) -> None:
    _write_publish_inputs(tmp_path)

    example_publish.run(dict(_JOB), tmp_path, {"node_key": "publish_content"})

    payload = json.loads((tmp_path / "publish_payload.json").read_text(encoding="utf-8"))
    assert payload["simulated"] is True
    assert payload["job_id"] == "job-demo-1"
    assert payload["workflow"]["key"] == "education_video_problems_generation"
    assert payload["workflow"]["revision_id"] == "rev-1"
    assert payload["knowledge_point"]["title"] == "演示知识点"
    assert payload["script"]["content"].startswith("## 开场导入")
    assert len(payload["exercises"]) == 5
    assert payload["script_review"]["verdict"] == "pass"
    assert payload["exercises_review"]["summary"] == "ok"


def test_publish_fails_fast_on_missing_input(tmp_path: Path) -> None:
    # Only knowledge_point.json present; script.md missing must fail the node.
    (tmp_path / "knowledge_point.json").write_text(
        json.dumps({"knowledge_point": {"id": "demo", "title": "t"}}), encoding="utf-8"
    )
    with pytest.raises((OSError, ValueError)):
        example_publish.run(dict(_JOB), tmp_path, {"node_key": "publish_content"})
