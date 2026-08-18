"""Re-validate completed Agent Worker node runs and mark validation failures.

Before EXEC-VALIDATION-001 the Host never ran the node skill's
``scripts/validate_output.py`` for Worker-executed nodes (only the local Pi
runner did), so review rejections and other contract violations silently
completed. This script re-runs the validator for every latest completed
Worker-run node; on failure it inserts a failed ``node_runs`` row
(``runner='validation-backfill'``), flips the ``job_nodes`` row to failed with
the classified category, marks downstream completed nodes stale, and fails the
job — the standard rerun-by-failure flow can then recover them. Idempotent:
recovered nodes are skipped because their latest run is no longer an
unvalidated Worker run.

Usage:
    uv run python -m scripts.backfill_worker_output_validation [--dry-run] \
        [--workspace-id ID] [--since YYYY-MM-DD] [--database-url DSN] \
        [--data-dir PATH]
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.failure_classification import classify_failure
from server.app.services.skill_source_store import SkillSourceStore
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.settings import load_settings
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.output_validation import run_output_validator
from server.app.workflows.skills import resolve_workflow_skill
from server.app.workflows.workflow_branching import downstream_nodes

BACKFILL_RUNNER = "validation-backfill"
STALE_REASON = "upstream output validation failed"
_MAX_ERROR_CHARS = 2000


def find_candidate_runs(
    database_dsn: DatabaseDsn,
    *,
    workspace_id: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Latest completed Worker run per (job, node) whose node is still completed."""
    clauses = [
        "r.status = 'completed'",
        "jn.status = 'completed'",
        "r.runner in (select worker_id from agent_workers)",
    ]
    params: list[Any] = []
    if workspace_id:
        clauses.append("j.workspace_id = %s")
        params.append(workspace_id)
    if since:
        clauses.append("r.finished_at >= %s::timestamptz")
        params.append(since)
    with read_connection(database_dsn) as conn:
        rows = conn.execute(
            f"""
            with latest as (
              select max(id) as id
              from node_runs
              where status = 'completed'
              group by job_id, node_key
            ),
            manifests as (
              select distinct on (node_run_id) node_run_id, manifest_json
              from agent_execution_requests
              where node_run_id is not null
              order by node_run_id, claimed_at desc nulls last
            )
            select r.id as run_id, r.job_id, r.node_key, r.skill_version, r.finished_at,
                   j.storage_dir, j.status as job_status, j.workflow_key,
                   j.workspace_id,
                   j.workflow_definition_snapshot_json,
                   m.manifest_json
            from latest
            join node_runs r on r.id = latest.id
            join jobs j on j.id = r.job_id
            join job_nodes jn on jn.job_id = r.job_id and jn.node_key = r.node_key
            left join manifests m on m.node_run_id = r.id
            where {" and ".join(clauses)}
            order by r.job_id, r.node_key
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _skill_for_run(row: dict[str, Any]) -> str:
    manifest = row.get("manifest_json")
    if manifest:
        payload = json.loads(manifest) if isinstance(manifest, str) else manifest
        skill = str(payload.get("skill", ""))
        if skill:
            return skill
    # Fallback: resolve the capability through the agent definition catalog.
    definition = definition_from_job_snapshot(
        {"workflow_definition_snapshot_json": row.get("workflow_definition_snapshot_json") or ""}
    )
    node = definition.nodes.get(str(row["node_key"])) if definition is not None else None
    return f"capability:{node.capability}" if node is not None else ""


def validate_run(
    skill_manager: SkillManager,
    jobs_dir: Path,
    row: dict[str, Any],
    *,
    capability_skills: dict[tuple[str, str], str],
) -> tuple[str, str]:
    """Run the skill validator for one candidate; return (verdict, message)."""
    skill = _skill_for_run(row)
    if skill.startswith("capability:"):
        # Agent definitions are workspace-scoped (schema v46): resolve the
        # capability inside the run's own workspace, never globally.
        workspace_id = str(row.get("workspace_id") or "")
        skill = capability_skills.get((workspace_id, skill.removeprefix("capability:")), "")
    if not skill:
        return "unknown", "no skill resolvable for node"
    job_dir = resolve_job_dir({"id": row["job_id"], "storage_dir": row["storage_dir"]}, jobs_dir)
    if not job_dir.is_dir():
        return "unknown", f"job dir missing: {job_dir}"
    try:
        # Read-only validation runs straight from the shared base dir; the
        # execution-private copy (and its serialized cache lock) is unneeded.
        skill_dir = resolve_workflow_skill(skill_manager.base_dir, skill)
    except Exception as exc:
        return "unknown", f"skill resolution failed: {exc}"
    error = run_output_validator(skill_dir, job_dir)
    if error is None:
        return "valid", ""
    if "Missing input file" in error:
        return "unknown", error
    return "invalid", error[:_MAX_ERROR_CHARS]


def mark_failed(
    database_dsn: DatabaseDsn,
    failures: list[dict[str, Any]],
) -> tuple[int, int]:
    """Mark each failed (job, node) plus stale downstreams; return (jobs, nodes) marked."""
    by_job: dict[str, list[dict[str, Any]]] = {}
    for row in failures:
        by_job.setdefault(str(row["job_id"]), []).append(row)

    marked = 0
    marked_nodes_total = 0
    with write_transaction(database_dsn) as conn:
        for job_id, rows in by_job.items():
            busy = conn.execute(
                """
                select 1 from job_nodes where job_id=%s and status='running'
                union
                select 1 from agent_execution_requests
                where job_id=%s and state in ('queued', 'claimed', 'reporting')
                """,
                (job_id, job_id),
            ).fetchone()
            if busy is not None:
                print(f"skip job {job_id}: nodes running or agent request active")
                continue

            definition = definition_from_job_snapshot(
                {
                    "workflow_definition_snapshot_json": rows[0].get(
                        "workflow_definition_snapshot_json"
                    )
                    or ""
                }
            )
            stale: set[str] = set()
            marked_nodes = 0
            for row in rows:
                node_key = str(row["node_key"])
                message = str(row["validation_error"])
                category, detail = classify_failure(1, message)
                updated = conn.execute(
                    """
                    update job_nodes
                    set status='failed', error_message=%s,
                        failure_category=%s, failure_detail=%s
                    where job_id=%s and node_key=%s and status='completed'
                    """,
                    (message, category, detail, job_id, node_key),
                )
                if updated.rowcount == 0:
                    print(f"skip {job_id}.{node_key}: node no longer completed")
                    continue
                conn.execute(
                    """
                    insert into node_runs(
                      job_id, node_key, status, finished_at, exit_code, error_message,
                      skill_version, runner, failure_category, failure_detail
                    )
                    values (%s, %s, 'failed', current_timestamp, 1, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        node_key,
                        message,
                        str(row.get("skill_version") or ""),
                        BACKFILL_RUNNER,
                        category,
                        detail,
                    ),
                )
                marked_nodes += 1
                if definition is not None:
                    stale.update(downstream_nodes(definition, node_key))
            stale -= {str(row["node_key"]) for row in rows}
            for node_key in sorted(stale):
                conn.execute(
                    """
                    update job_nodes
                    set status='stale', stale_reason=%s
                    where job_id=%s and node_key=%s and status='completed'
                    """,
                    (STALE_REASON, job_id, node_key),
                )
            failed_now = conn.execute(
                "select 1 from job_nodes where job_id=%s and status='failed'",
                (job_id,),
            ).fetchone()
            if failed_now is not None:
                conn.execute(
                    """
                    update jobs
                    set status='failed', outcome='', updated_at=current_timestamp
                    where id=%s and status != 'running'
                    """,
                    (job_id,),
                )
                marked += 1
            marked_nodes_total += marked_nodes
    return marked, marked_nodes_total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not mark.")
    parser.add_argument("--workspace-id", default=None, help="Limit to one workspace.")
    parser.add_argument(
        "--since",
        default=None,
        help="Only examine runs finished at/after this date (YYYY-MM-DD).",
    )
    parser.add_argument("--database-url", default=None, help="Override configured DSN.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Data dir holding jobs/ (defaults to the configured data dir).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel validator processes (default 8).",
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir
    skill_manager = SkillManager(
        store=SkillSourceStore(dsn),
        base_dir=Path.home() / ".agents" / "skills" / "agent-legion",
    )

    capability_skills: dict[tuple[str, str], str] = {}
    with read_connection(dsn) as conn:
        for row in conn.execute(
            "select workspace_id, definition_json from versioned_entities"
            " where entity_type='agent' and status='published'"
        ).fetchall():
            payload = row["definition_json"]
            payload = json.loads(payload) if isinstance(payload, str) else payload
            capability = str(payload.get("capability", ""))
            if capability and row["workspace_id"]:
                capability_skills[(str(row["workspace_id"]), capability)] = str(
                    payload.get("skill", "")
                )

    candidates = find_candidate_runs(dsn, workspace_id=args.workspace_id, since=args.since)
    print(f"candidate worker-completed node runs: {len(candidates)}")

    failures: list[dict[str, Any]] = []
    valid = unknown = 0

    def _check(row: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        verdict, message = validate_run(
            skill_manager, data_dir / "jobs", row, capability_skills=capability_skills
        )
        return row, verdict, message

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for row, verdict, message in pool.map(_check, candidates):
            done += 1
            if done % 1000 == 0:
                print(f"progress {done}/{len(candidates)}", flush=True)
            if verdict == "valid":
                valid += 1
            elif verdict == "unknown":
                unknown += 1
                print(f"unknown {row['job_id']}.{row['node_key']}: {message}", flush=True)
            else:
                row["validation_error"] = message
                failures.append(row)
                print(f"INVALID {row['job_id']}.{row['node_key']}: {message.splitlines()[0]}")
    print(f"valid={valid} invalid={len(failures)} unknown={unknown}")

    if args.dry_run:
        print(f"dry-run: {len({r['job_id'] for r in failures})} job(s) would be marked failed")
        return
    marked, marked_nodes = mark_failed(dsn, failures)
    print(f"marked {marked} job(s) failed across {marked_nodes} node(s)")


if __name__ == "__main__":
    main()
