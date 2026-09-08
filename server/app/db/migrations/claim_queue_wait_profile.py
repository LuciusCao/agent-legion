"""Schema v81: claim queue-wait gauge columns on ops_runtime_profile_samples.

Issue #551 (supply→consume pipeline observability): the claim path's
forensic split (#448 v78: scan/evaluate/writes) covers the transaction, but
not the wait BEFORE it — how long a queued request sat before a Worker
picked it up. ``evaluate_candidate`` folds each promote's queue wait into
the claim gauge family (``queue_wait`` stage, stage_gauges.CLAIM_STAGES);
this migration adds the two persisted columns the sampler writes.

Same guarded-ALTER home rule as v78/v80: the columns live ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay, and
both install paths run the chain anyway.
"""

from __future__ import annotations

from typing import Any

_CLAIM_QUEUE_WAIT_COLUMNS_DDL = """
alter table ops_runtime_profile_samples
  add column if not exists claim_queue_wait_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists claim_queue_wait_seconds_max double precision not null default 0;
"""


def migrate_claim_queue_wait_profile(conn: Any) -> None:
    """Add the claim queue-wait gauge columns (v81, #551)."""
    conn.execute(_CLAIM_QUEUE_WAIT_COLUMNS_DDL)
