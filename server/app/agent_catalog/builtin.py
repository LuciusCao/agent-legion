"""Built-in Agent definition templates for the demo workflow.

Agent definitions are workspace-scoped (schema v46): nothing here is seeded
globally anymore. This module pins the factory *templates* for the
open-source demo workflow ``education_video_problems_generation``; the demo
seed (``seed_demo_workspace_agent_definitions``) instantiates them into a
workspace that binds the demo workflow, seed-if-absent, so admin edits and
archivals inside the workspace are never overwritten or resurrected.

Execution configuration (provider/model/thinking) is deliberately NOT part of
these definitions — the loader merges the workflow top-level ``execution``
defaults into every non-start node (code nodes simply never read them) and
node ``execution.*`` overrides win; workspace-level defaults were retired at
schema v64. The demo expects the operator to configure execution in Studio.
Skill bindings live on the demo DAG nodes (issue #76), not on these
definitions; the referenced skills resolve to the local source roots
imported by ``make import-demo`` (see ``server.app.skills.builtin_sources``).
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
    """Publish each built-in demo agent into the workspace when absent.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187); production callers pass the facade.
    Called when a workspace binds the demo workflow (workspace create /
    workflow switch). Seed-if-absent: an agent the admin already touched in
    this workspace (published a new definition, or archived every version to
    disable it) is never overwritten or resurrected — only a completely
    absent entity key gets the factory definition. Returns the agent IDs
    seeded this run.
    """
    service = AgentService(database_dsn, workspace_id)
    seeded: list[str] = []
    for agent_id, definition in BUILTIN_AGENT_DEFINITIONS.items():
        if service.list_versions(agent_id):
            continue
        service.save_draft(agent_id, definition, created_by="system")
        service.publish(agent_id)
        seeded.append(agent_id)
    return seeded
