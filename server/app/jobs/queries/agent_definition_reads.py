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

from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.db.transaction import read_connection
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


#: Active revisions with at least one self-contained agent node (#933): the
#: loader bakes the workflow top-level ``execution.runtime`` default into
#: every agent node, so the persisted snapshot's per-node runtime is the
#: effective one. Malformed JSON / a non-object ``nodes`` read as "none" (the
#: CASE guards keep the cast and jsonb_each total; no jsonpath filter — its
#: ``?`` would trip the legacy-placeholder guard).
_SELF_CONTAINED_AGENT_NODES_SQL = """
select exists(
  select 1
  from workflow_revisions wr
  cross join lateral (
    select case when pg_input_is_valid(wr.definition_json, 'jsonb')
                then wr.definition_json::jsonb -> 'nodes' end as nodes
  ) d
  cross join lateral jsonb_each(
    case when jsonb_typeof(d.nodes) = 'object' then d.nodes else '{}'::jsonb end
  ) n
  where wr.status = 'active'
    and n.value ->> 'node_type' = 'agent'
    and coalesce(n.value -> 'execution' ->> 'runtime', '') <> ''
) as has_any
"""


def has_self_contained_agent_nodes(connect_source: ConnectSource) -> bool:
    """Cross-workspace probe for poll-loop scan gates (never for resolution)."""
    with read_connection(resolve_dsn(connect_source)) as conn:
        row = conn.execute(_SELF_CONTAINED_AGENT_NODES_SQL).fetchone()
    return bool(row["has_any"]) if row is not None else False
