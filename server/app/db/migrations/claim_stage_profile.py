"""Schema v78: claim-stage latency columns on ops_runtime_profile_samples.

Issue #448 phase 1 adds the claim-path forensic instrumentation: one claim
transaction's round-trip splits into worker_setup / scan / evaluate / writes
/ commit stages (``server/app/agent_broker/claim_timing.py``), and the #359
runtime-profile sampler folds the per-stage totals and maxes into the
per-minute bucket next to the existing claim-wide gauges
(``claim_seconds_total`` / ``claim_seconds_max``). The stage split is the
data that decides phase 2's priority (transaction slimming vs worker-side
concurrency vs event-driven wakeups), so it must survive past the log line.

The columns live ONLY in this migration's guarded ``add column if not
exists`` (idempotent on replay), not in postgres_schema.sql's CREATE TABLE:
the schema file sits at its budget ceiling (the same squeeze #437's v77
round resolved by moving the trigger DDL into its migration), and both
install paths run this migration anyway — fresh installs replay every
migration in order, upgrades run versions above the high-water mark.
"""

from __future__ import annotations

from typing import Any

_CLAIM_STAGE_COLUMNS_DDL = """
alter table ops_runtime_profile_samples
  add column if not exists claim_scan_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists claim_scan_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists claim_evaluate_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists claim_evaluate_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists claim_writes_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists claim_writes_seconds_max double precision not null default 0;
"""


def migrate_claim_stage_profile(conn: Any) -> None:
    """Add the claim-stage gauge columns (v78, #448 phase 1)."""
    conn.execute(_CLAIM_STAGE_COLUMNS_DDL)
