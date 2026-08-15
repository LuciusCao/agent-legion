"""Built-in executor definitions (retired ``config/workflow.yaml`` executors section).

Executor definitions are versioned entities (``versioned_entities``,
``entity_type='executor'``) managed in Studio and hydrated from the DB at
startup. This module pins the factory catalog: the seed path
(``executor_definition_service.seed_builtin_executor_definitions``) publishes
these definitions when an executor has no published row yet, so existing
deployments keep running unchanged and admin edits are never overwritten.

The raw shape below feeds ``load_executor_definitions`` directly; keep it
field-for-field equivalent to the last tracked yaml.
"""

from __future__ import annotations

from typing import Any

from server.app.executors.builtin_demo import DEMO_CODE_CAPABILITIES

BUILTIN_EXECUTOR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "code-default": {
        "kind": "code",
        "global_capacity": 16,
        "capabilities": {
            # Demo workflow (education_video_problems_generation) code nodes
            # (see builtin_demo.py).
            **DEMO_CODE_CAPABILITIES,
        },
    },
}
