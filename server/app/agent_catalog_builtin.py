"""Built-in Agent definitions: the demo workflow's factory agent catalog.

Agents have no legacy yaml seed (the retired ``agents:`` section was never
transcribed into code): this module pins the factory definitions for the
open-source demo workflow ``education_video_problems_generation`` so a fresh
deployment can run the example out of the box. The seed path
(``agent_service.seed_builtin_agent_definitions``) publishes each definition
only when the agent id has no versioned row at all, so admin edits and
archivals are never overwritten or resurrected.

Execution configuration (provider/model/thinking) is deliberately NOT part of
these definitions — it resolves per node from node ``execution.*`` overrides
to workspace defaults (``default_agent_*``); the demo expects the operator to
configure workspace defaults. Skills resolve to the local source roots
imported by ``make import-demo`` (see ``server.app.skills.builtin_sources``).
"""

from __future__ import annotations

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import DatabaseDsn
from server.app.services.agent_service import AgentService

_DEMO_SKILL_PREFIX = "education-video-problems-generation"

BUILTIN_AGENT_DEFINITIONS: dict[str, AgentDefinition] = {
    agent_id: AgentDefinition(capability=capability, runtime="velites", skill=skill)
    for agent_id, capability, skill in [
        (
            "example-write-script-v1",
            "write_script",
            f"{_DEMO_SKILL_PREFIX}/write-script",
        ),
        (
            "example-review-script-v1",
            "review_script",
            f"{_DEMO_SKILL_PREFIX}/review-script",
        ),
        (
            "example-generate-questions-v1",
            "generate_questions",
            f"{_DEMO_SKILL_PREFIX}/generate-questions",
        ),
        (
            "example-review-questions-v1",
            "review_questions",
            f"{_DEMO_SKILL_PREFIX}/review-questions",
        ),
    ]
}


def seed_builtin_agent_definitions(database_dsn: DatabaseDsn) -> list[str]:
    """Publish each built-in agent that has no versioned row yet.

    Seed-if-absent, mirroring the executor seed
    (``executor_definition_service.seed_builtin_executor_definitions``): an
    agent the admin already touched (published a new definition, or archived
    every version to disable it) is never overwritten or resurrected — only a
    completely absent entity key gets the factory definition. Returns the
    agent IDs seeded this run.
    """
    service = AgentService(database_dsn)
    seeded: list[str] = []
    for agent_id, definition in BUILTIN_AGENT_DEFINITIONS.items():
        if service.list_versions(agent_id):
            continue
        service.save_draft(agent_id, definition, created_by="system")
        service.publish(agent_id)
        seeded.append(agent_id)
    return seeded
