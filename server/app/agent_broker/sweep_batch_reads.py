"""Batched reads for one ``sweep_expired_claims`` batch (#957).

Split out of ``sweepers.py`` (file budget). The sweep takes every job's
job-mutation advisory lock first, in the global batch order, and only then
calls :func:`batch_reads` — so each row's generation / lease status is still
read while its job lock is held, exactly like the former per-row
lock → select → select sequence, just in two statements per batch instead of
two per row.
"""

from __future__ import annotations

from typing import Any

# Per-cycle cap on expired claims handled by one sweep transaction; the rest
# (oldest heartbeat first) is picked up by the next sweep cycle.
SWEEP_BATCH_LIMIT = 500


def batch_reads(conn: Any, rows: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, str]]:
    """(jobs.execution_generation per job, executor_leases.status per lease).

    Missing jobs / leases are simply absent (callers read ``.get`` → None,
    the same "no row" outcome the per-row ``fetchone()`` produced)."""
    job_ids = sorted({str(r["job_id"]) for r in rows})
    lease_ids = sorted({str(r["lease_id"]) for r in rows if r["lease_id"] is not None})
    generations: dict[str, int] = {}
    if job_ids:
        found = conn.execute(
            "select id, execution_generation from jobs where id = any(%s)", (job_ids,)
        ).fetchall()
        generations = {str(r["id"]): int(r["execution_generation"]) for r in found}
    leases: dict[str, str] = {}
    if lease_ids:
        found = conn.execute(
            "select id, status from executor_leases where id = any(%s)", (lease_ids,)
        ).fetchall()
        leases = {str(r["id"]): str(r["status"]) for r in found}
    return generations, leases
