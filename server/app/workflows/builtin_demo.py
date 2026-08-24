"""Open-source demo workflow DAG: ``education_video_problems_generation``.

Knowledge-point material (the ``examples/education-video-problems-generation/``
markdown, seeded into the demo workspace as sample materials — design §9)
→ teaching-video script + review → five exercises + review → simulated
publish. No external service required; see ``examples/README.md``. Kept in
its own module so the business DAGs in ``builtin.py`` stay untouched by demo
edits (and vice versa).

Same constraint as ``builtin.py``: dependency-minimal (definition models
only), no settings or database access.
"""

from __future__ import annotations

from typing import Any

DEMO_WORKFLOW_KEY = "education_video_problems_generation"

DEMO_WORKFLOW_DEFINITION: dict[str, Any] = {
    "key": DEMO_WORKFLOW_KEY,
    "label": "教学视频脚本与题目生成（示例）",
    "schema_version": 2,
    # No legacy intake modes (retired in #154): the demo's only path is
    # material/ref items — the user uploads (or picks the seeded sample)
    # knowledge-point markdown and each material becomes one job; the intake
    # node reads it via ctx.material.
    "nodes": {
        "intake_knowledge_points": {
            "label": "读取知识点",
            "capability": "intake_knowledge_points",
            "after": [],
            "inputs": [],
            "outputs": ["knowledge_point.json"],
        },
        "write_script": {
            "label": "撰写教学视频脚本",
            "capability": "write_script",
            "after": ["intake_knowledge_points"],
            "inputs": ["knowledge_point.json"],
            "outputs": ["script.md"],
        },
        "review_script": {
            "label": "评审脚本",
            "capability": "review_script",
            "after": ["write_script"],
            "inputs": ["knowledge_point.json", "script.md"],
            "outputs": ["script_review.json"],
        },
        "generate_questions": {
            "label": "生成练习题",
            "capability": "generate_questions",
            "after": ["intake_knowledge_points"],
            "inputs": ["knowledge_point.json"],
            "outputs": ["exercises.json"],
        },
        "review_questions": {
            "label": "评审练习题",
            "capability": "review_questions",
            "after": ["generate_questions"],
            "inputs": ["knowledge_point.json", "exercises.json"],
            "outputs": ["exercises_review.json"],
        },
        "publish_content": {
            "label": "汇总并模拟入库",
            "capability": "publish_content",
            "after": ["review_script", "review_questions"],
            "inputs": [
                "knowledge_point.json",
                "script.md",
                "script_review.json",
                "exercises.json",
                "exercises_review.json",
            ],
            "outputs": ["publish_payload.json"],
            "terminal": {"outcome": "published"},
        },
    },
    "edges": [
        {"from": "intake_knowledge_points", "to": "write_script"},
        {"from": "intake_knowledge_points", "to": "generate_questions"},
        {"from": "write_script", "to": "review_script"},
        {"from": "generate_questions", "to": "review_questions"},
        {"from": "review_script", "to": "publish_content"},
        {"from": "review_questions", "to": "publish_content"},
    ],
}
