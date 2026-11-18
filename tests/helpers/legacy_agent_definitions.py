"""Historical AgentDefinition rows for legacy resolver/replay/read tests.

Self-contained workflow tests must not seed these rows: their execution profile
is carried by the workflow node and does not resolve through the Agent catalog.
"""

from __future__ import annotations

from tests.helpers import publish_agent_definition, save_agent_definition_draft


def seed_workspace_agent_definitions(workspace_id: str) -> list[str]:
    """Seed absent legacy demo rows for tests that exercise old resolvers.

    Existing versions, including archived ones, are left untouched.
    """
    from server.app.agent_catalog.builtin import BUILTIN_AGENT_DEFINITIONS
    from server.app.services.versioned_entities import VersionedEntityStore
    from tests.postgres_support import TEST_DATABASE_URL

    store = VersionedEntityStore(TEST_DATABASE_URL, "agent")
    seeded: list[str] = []
    for agent_id, definition in BUILTIN_AGENT_DEFINITIONS.items():
        if store.list_versions(agent_id, workspace_id):
            continue
        saved = save_agent_definition_draft(
            TEST_DATABASE_URL, workspace_id, agent_id, definition, created_by="system"
        )
        publish_agent_definition(TEST_DATABASE_URL, workspace_id, agent_id, saved.definition_hash)
        seeded.append(agent_id)
    return seeded
