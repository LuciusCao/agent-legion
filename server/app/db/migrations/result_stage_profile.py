"""Schema v80: result-stage latency columns on ops_runtime_profile_samples.

Issue #521 adds the result-commit forensic instrumentation, mirroring the
claim-stage split (#448 phase 1, v78): one Agent result commit splits into
unpack / artifacts_verify / validate / artifacts_upload / lease_write /
events / mark_done stages (``server/app/agent_broker/result_timing.py``),
and the #359 runtime-profile sampler folds the per-stage totals and maxes
into the per-minute bucket next to the existing result-wide gauges
(``result_seconds_total`` / ``result_seconds_max``). The stage split is
the data that orders the follow-up slimming (the events.jsonl
single-pass merge reserved for 0.7.3 will be justified from the
``events`` columns), so it must survive past the log line.

The columns live ONLY in this migration's guarded ``add column if not
exists`` (idempotent on replay), not in postgres_schema.sql's CREATE
TABLE — the same DDL-home rule v78 established (the schema file sits at
its budget ceiling; both install paths run this migration anyway).
"""

from __future__ import annotations

from typing import Any

_RESULT_STAGE_COLUMNS_DDL = """
alter table ops_runtime_profile_samples
  add column if not exists result_unpack_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_unpack_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_artifacts_verify_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_artifacts_verify_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_validate_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_validate_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_artifacts_upload_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_artifacts_upload_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_lease_write_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_lease_write_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_events_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_events_seconds_max double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_mark_done_seconds_total double precision not null default 0;
alter table ops_runtime_profile_samples
  add column if not exists result_mark_done_seconds_max double precision not null default 0;
"""


def migrate_result_stage_profile(conn: Any) -> None:
    """Add the result-stage gauge columns (v80, #521)."""
    conn.execute(_RESULT_STAGE_COLUMNS_DDL)
