"""Demo workflow code capabilities of the ``code-default`` executor.

The two demo code nodes of the open-source demo workflow
(``education_video_problems_generation``): pure-stdlib, network-free. Since
#96 retired the capability ``path`` binding, the capabilities are
custom-code-only: their code publishes at startup as global node_code
versions seeded from the git-reviewed ``workflow_nodes/`` sources
(``services/demo_node_seed.py``). Kept in its own module so demo edits never
touch the business capability table in ``builtin_definitions.py``.

The raw shape feeds the ``capabilities`` map of an executor definition; see
``builtin_definitions.py`` for the seed semantics.
"""

from __future__ import annotations

from typing import Any

DEMO_CODE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "intake_knowledge_points": {
        "config_schema": {
            "type": "object",
            "properties": {
                "knowledge_dir": {
                    "type": "string",
                    "default": "examples/education-video-problems-generation",
                    "description": "知识点 markdown 目录（相对路径按 Host 根目录解析；出厂默认值，可被节点/workspace 覆盖）",
                },
            },
        },
    },
    "publish_content": {},
}
