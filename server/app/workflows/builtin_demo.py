"""Open-source demo workflow DAG: ``education_video_problems_generation``.

Knowledge-point markdown (``examples/education-video-problems-generation/``)
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
    "intake": {
        "modes": {
            # entity "question" + mode "direct_ids" resolves through the
            # platform's direct.question_ids resolver: one job per input
            # value, the value being a knowledge-point file stem.
            "direct_ids": {
                "label": "按知识点批量",
                "input_field": "knowledge_point_ids",
            },
        },
    },
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
