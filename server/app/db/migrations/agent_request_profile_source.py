"""Schema v92 (#933, #440 P2): Agent request profile source columns.

Self-contained agent nodes (``execution.runtime`` declared in the workflow)
dispatch from their own execution profile instead of a published Agent
definition. Their queued request rows cannot join ``versioned_entities`` at
claim, so the frozen profile rides the row:

- ``profile_source`` — ``agent_definition`` (default: every existing row and
  every legacy-sourced enqueue) or ``node``;
- ``runtime`` / ``requires_labels_json`` — the node profile's runtime and
  Worker label requirements (NULL on legacy rows, which keep reading them
  from the joined definition).

Node-sourced rows keep the NOT NULL identity columns meaningful:
``agent_id`` = node key (stock buckets and Worker event labels unchanged)
and ``agent_definition_hash`` = profile hash (#645 identity chain).

DDL-only, guarded-ALTER home rule (v87/v89/v90): the columns live ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay.
Rollback to 0.7.15 needs no down step: old binaries ignore the columns, and
their stale-definition sweep fails queued node rows (no matching published
Agent) with an explicit reason — see docs/remote-execution-runbook.md.
"""

from __future__ import annotations

from typing import Any

_PROFILE_SOURCE_DDL = """
alter table agent_execution_requests
  add column if not exists profile_source text not null default 'agent_definition';
alter table agent_execution_requests
  add column if not exists runtime text;
alter table agent_execution_requests
  add column if not exists requires_labels_json text;
alter table agent_execution_requests
  drop constraint if exists agent_execution_requests_profile_source_check;
alter table agent_execution_requests
  add constraint agent_execution_requests_profile_source_check
  check(profile_source in ('agent_definition', 'node'));
"""


def migrate_agent_request_profile_source(conn: Any) -> None:
    """Add profile_source / runtime / requires_labels_json (v92)."""
    conn.execute(_PROFILE_SOURCE_DDL)
