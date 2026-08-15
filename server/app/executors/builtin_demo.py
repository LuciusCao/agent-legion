"""Demo workflow code capabilities of the ``code-default`` executor.

The two built-in code nodes of the open-source demo workflow
(``education_video_problems_generation``): pure-stdlib, network-free
(EXEC-CODE-001). Kept in its own module so demo edits never touch the
business capability table in ``builtin_definitions.py``.

The raw shape feeds the ``capabilities`` map of an executor definition; see
``builtin_definitions.py`` for the seed semantics.
"""

from __future__ import annotations

from typing import Any

DEMO_CODE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "intake_knowledge_points": {
        "path": "workflow_nodes/example_intake.py",
        "config_schema": {
            "type": "object",
            "properties": {
                "knowledge_dir": {
                    "type": "string",
                    "default": "examples/education-video-problems-generation",
                    "description": "知识点 markdown 目录（相对路径按仓库根解析；出厂默认值，可被节点/workspace 覆盖）",
                },
            },
        },
    },
    "publish_content": {
        "path": "workflow_nodes/example_publish.py",
    },
}
