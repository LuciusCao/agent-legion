"""Self-contained agent node probe for the poll-loop scan gates (#933).

Split from ``agent_definition_reads`` (file budget). The agent node profile
facade (``services/agent_node_profile_catalog.agent_profiles_may_exist``)
combines it with the published-Agent probe: a workspace with no Agent
definitions must still scan its self-contained agent candidates.
"""

from __future__ import annotations

from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.db.transaction import read_connection

#: Revisions with at least one self-contained agent node (#933) that some
#: job may still dispatch from: the workspace's ACTIVE revision, or any
#: revision a runnable job is pinned to (the profile is frozen with the job
#: snapshot, so publishing a later legacy-only revision must not strand the
#: in-flight self-contained nodes — PR #1039 codex R1). The loader bakes the
#: workflow top-level ``execution.runtime`` default into every agent node, so
#: the persisted snapshot's per-node runtime is the effective one. Malformed
#: JSON / a non-object ``nodes`` read as "none" (the CASE guards keep the
#: cast and jsonb_each total; no jsonpath filter — its ``?`` would trip the
#: legacy-placeholder guard).
_SELF_CONTAINED_AGENT_NODES_SQL = """
with candidate_revisions as (
  select wr.id, wr.definition_json from workflow_revisions wr where wr.status = 'active'
  union all
  select wr.id, wr.definition_json
  from (
    select distinct j.workflow_revision_id as revision_id from jobs j
    where j.status in ('queued', 'running', 'awaiting_approval')
      and j.workflow_revision_id <> ''
  ) pinned
  join workflow_revisions wr on wr.id = pinned.revision_id
)
select exists(
  select 1
  from candidate_revisions wr
  cross join lateral (
    select case when pg_input_is_valid(wr.definition_json, 'jsonb')
                then wr.definition_json::jsonb -> 'nodes' end as nodes
  ) d
  cross join lateral jsonb_each(
    case when jsonb_typeof(d.nodes) = 'object' then d.nodes else '{}'::jsonb end
  ) n
  where n.value ->> 'node_type' = 'agent'
    and coalesce(n.value -> 'execution' ->> 'runtime', '') <> ''
) as has_any
"""


def has_self_contained_agent_nodes(connect_source: ConnectSource) -> bool:
    """Cross-workspace probe for poll-loop scan gates (never for resolution)."""
    with read_connection(resolve_dsn(connect_source)) as conn:
        row = conn.execute(_SELF_CONTAINED_AGENT_NODES_SQL).fetchone()
    return bool(row["has_any"]) if row is not None else False
