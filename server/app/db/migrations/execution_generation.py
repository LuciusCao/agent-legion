"""Schema v85 (#759): execution generation columns.

``jobs.execution_generation`` is the monotone execution-epoch source of
truth: every mutation that resets execution state (workflow upgrade, rerun,
run-to, approval rework) increments it inside the same
``lease_guarded_mutation`` transaction that holds the
``job-mutation:<job_id>`` advisory lock. Every execution-state write path
(code/agent claim, finish, fail-without-lease, approval park/decide)
acquires the same lock, re-reads the generation, and CAS-compares it
against the epoch its request was stamped with — a stale candidate from a
pre-reset epoch can no longer flip a node the reset just rebuilt
(EXEC-GENERATION-001).

Mirror columns stamp the epoch onto the rows each path already owns:
``agent_execution_requests`` at enqueue, ``executor_leases``/``node_runs``
at claim (the finish CAS compares the lease stamp against the live job
epoch, so a late finish from an old epoch releases its lease and records
its run but cannot touch ``job_nodes``), and ``job_nodes`` at reset/park
(approval gates compare it against the live epoch).

This module owns the DDL — postgres_schema.sql sits at its raw-line
ceiling (the v76/v84 precedent): fresh and pre-v85 databases both run
this apply fn, and the parity test pins the shapes equal. Default 0 keeps
legacy rows consistent: a pre-v85 in-flight lease and its job share epoch
0, so the new CAS checks admit them unchanged.
"""

from __future__ import annotations

from typing import Any

_EXECUTION_GENERATION_DDL = """
alter table jobs add column if not exists execution_generation integer not null default 0;
alter table job_nodes add column if not exists execution_generation integer not null default 0;
alter table node_runs add column if not exists execution_generation integer not null default 0;
alter table executor_leases add column if not exists execution_generation integer not null default 0;
alter table agent_execution_requests add column if not exists execution_generation integer not null default 0;
"""


def migrate_execution_generation(conn: Any) -> None:
    """Add the execution-generation epoch columns (v85, #759); idempotent."""
    conn.execute(_EXECUTION_GENERATION_DDL)
