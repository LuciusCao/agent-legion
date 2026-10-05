"""Uncached published-Agent catalog read on the JobQueries facade (#932).

The agent node profile facade (``services/agent_node_profile_catalog``)
reads the published catalog through here when a caller needs post-publish
truth instead of the ~5s process cache (workflow upgrade identity checks,
#645 P1-1), so the service layer never touches the store directly
(BOUNDARY-DATA-001).
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class AgentDefinitionReadQueriesMixin(ConnectionQueriesMixin):
    """Read-only queries for published ``versioned_entities`` Agent rows."""

    def published_agent_definition_documents(self, workspace_id: str) -> dict[str, dict[str, Any]]:
        """agent_id → published definition document of one workspace (uncached)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                """
                select entity_key, definition_json from versioned_entities
                where entity_type='agent' and workspace_id=%s and status='published'
                """,
                (workspace_id,),
            ).fetchall()
        return {str(row["entity_key"]): json.loads(row["definition_json"]) for row in rows}
