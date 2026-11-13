"""Legacy demo Agent templates for compatibility tests and fixtures.

Production demo and workflow-revision seeding no longer instantiates these
definitions: the demo DAG carries self-contained agent node profiles instead.
``seed_demo_workspace_agent_definitions`` remains only for the existing test
helper and explicit legacy-history setup; a later test-migration slice owns
its removal. Historical rows stay readable by old job/replay resolvers.

The demo nodes declare ``execution.runtime`` individually and bind their
skills directly (issue #76). Provider/model/thinking remain operator-owned
node execution settings, not properties of these historical definitions.
"""

from __future__ import annotations

from server.app.agent_catalog import AgentDefinition
from server.app.db.dialect import ConnectSource
from server.app.services.agent_service import AgentService

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


def seed_demo_workspace_agent_definitions(
    database_dsn: ConnectSource, workspace_id: str
) -> list[str]:
    """Publish legacy templates for a test fixture or explicit history setup.

    This is not a production seed entry point. Seed-if-absent preserves admin
    edits and archived rows; only a completely absent entity key is created.
    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187). Returns the agent IDs seeded this run.
    """
    service = AgentService(database_dsn, workspace_id)
    seeded: list[str] = []
    for agent_id, definition in BUILTIN_AGENT_DEFINITIONS.items():
        if service.list_versions(agent_id):
            continue
        saved = service.save_draft(agent_id, definition, created_by="system")
        # #841: assert the draft this seed just saved (no hash-less publish).
        service.publish(agent_id, saved.definition_hash)
        seeded.append(agent_id)
    return seeded
