"""Legacy demo Agent definition expectations for compatibility tests.

Production demo and workflow-revision seeding never instantiates these
definitions: the demo DAG carries self-contained agent node profiles instead.
Tests that exercise historical rows and old resolvers construct them through
``tests.helpers`` and the generic ``VersionedEntityStore``.

The demo nodes declare ``execution.runtime`` individually and bind their
skills directly (issue #76). Provider/model/thinking remain operator-owned
node execution settings, not properties of these historical definitions.
"""

from __future__ import annotations

from server.app.agent_catalog import AgentDefinition

DEMO_WORKFLOW_KEY = "education_video_problems_generation"

BUILTIN_AGENT_DEFINITIONS: dict[str, AgentDefinition] = {
    agent_id: AgentDefinition(capability=capability, runtime="velites")
    for agent_id, capability in [
        ("example-write-script-v1", "write_script"),
        ("example-review-script-v1", "review_script"),
        ("example-generate-questions-v1", "generate_questions"),
        ("example-review-questions-v1", "review_questions"),
    ]
}
