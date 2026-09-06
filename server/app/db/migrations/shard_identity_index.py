"""Schema v79: shard-aware one-active-request index (#401).

``idx_agent_requests_one_active_node`` deduplicated active requests by
(job_id, node_key). Remote shard executions (#389) carry their identity only
in the manifest top level (``shard_index``) — the index knew nothing of it,
so every shard node had at most ONE remote shard in flight: large fan-outs
serialized into N poll passes and a fleet's ``max_code_concurrency`` idled.

The replacement widens the uniqueness to the execution identity: a manifest
WITHOUT ``shard_index`` keeps the old one-active-per-(job, node) semantics
(the COALESCE fallback -1 is a real value — Postgres unique indexes treat
NULLs as never-equal, so a bare ``(…)::integer`` expression would let
unlimited non-shard rows through), a manifest WITH it dedups per
(job_id, node_key, shard_index), matching the ``node_shards`` row-level
dedup. The partial predicate (state in queued/claimed/reporting) is
unchanged; ``reporting`` still blocks re-enqueue until the result commits.

The expression must stay byte-identical to the one in
``postgres_schema.sql`` (fresh installs) — both paths share the semantics,
and the SQL fragment is re-exported from ``manifest_guard`` for the claim
side's active-request gate (one canonical expression, not two). The
schema-file replay runs FIRST on every upgrade and drops+recreates the
index under the same name, so by the time this apply fn runs the new shape
already exists — its job on the upgrade path is only to confirm presence
(probe, no DDL), keeping the index build from running twice (#501). The
drop+create DDL stays for a chain-only run without the replay.
"""

from __future__ import annotations

from typing import Any

# The active-row shard identity: manifests without a top-level shard_index
# (non-shard rows of every kind) collapse to -1; shard rows keep their index.
# COALESCE is load-bearing — see the module docstring's NULL note.
SHARD_IDENTITY_SQL = "coalesce((manifest_json::jsonb ->> 'shard_index')::integer, -1)"

_INDEX_NAME = "idx_agent_requests_one_active_node"

_INDEX_DDL = f"""
drop index if exists {_INDEX_NAME};
create unique index if not exists {_INDEX_NAME}
  on agent_execution_requests(job_id, node_key, {SHARD_IDENTITY_SQL})
  where state in ('queued', 'claimed', 'reporting');
"""


def migrate_shard_identity_index(conn: Any) -> None:
    """Widen the one-active-request index to the shard identity (v79, #401).

    The upgrade path arrives here AFTER the schema-file replay, which already
    rebuilt the index in its new shape (#501): the presence probe is then a
    no-op. Only when the probe misses (direct migration-chain run without the
    replay) does the drop+create DDL execute.
    """
    present = conn.execute(
        "select 1 from pg_indexes where schemaname=current_schema() and indexname=%s",
        (_INDEX_NAME,),
    ).fetchone()
    if present is None:
        conn.execute(_INDEX_DDL)
