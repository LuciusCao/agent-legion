from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.services.token_usage_pricing import calculate_cost

_NO_USAGE_REASON = "no token usage recorded for run"


def _currency_from_config(config: dict[str, Any]) -> str:
    return str(config.get("token_usage", {}).get("currency", "")).strip()


def _cost_breakdown(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    total_tokens: int,
    provider: str,
    model: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    return calculate_cost(
        total_tokens,
        input_tokens,
        output_tokens,
        cache_read_tokens,
        provider,
        model,
        config,
    ).model_dump()


def _usage_dict(row: Mapping[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    provider = str(row.get("provider", ""))
    model = str(row.get("model", ""))
    input_tokens = int(row.get("input_tokens", 0))
    output_tokens = int(row.get("output_tokens", 0))
    cache_read_tokens = int(row.get("cache_read_tokens", 0))
    total_tokens = int(row.get("total_tokens", 0))
    return {
        "node_run_id": int(row["node_run_id"]),
        "node_key": str(row.get("node_key", "")),
        "provider": provider,
        "model": model,
        "skill_version": str(row.get("skill_version", "")),
        "message_count": int(row.get("message_count", 0)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": total_tokens,
        "cost": _cost_breakdown(
            input_tokens,
            output_tokens,
            cache_read_tokens,
            total_tokens,
            provider,
            model,
            config,
        ),
        "is_complete": bool(row.get("is_complete", 1)),
        "usage_source": str(row.get("usage_source", "events_jsonl")),
    }


def build_run_usage_response(
    job_db: Any,
    run: Mapping[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    run_id = int(run["id"])
    job_id = str(run["job_id"])
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select * from node_run_token_usage where node_run_id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        return {
            "job_id": job_id,
            "run_id": run_id,
            "usage": None,
            "reason": _NO_USAGE_REASON,
        }
    return {
        "job_id": job_id,
        "run_id": run_id,
        "usage": _usage_dict(dict(row), config),
        "reason": None,
    }


def build_job_usage_response(
    job_db: Any,
    job_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    runs = job_db.list_node_runs(job_id)
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select * from node_run_token_usage where job_id=?",
            (job_id,),
        ).fetchall()
    usage_by_run = {int(row["node_run_id"]): dict(row) for row in rows}
    for usage_row in usage_by_run.values():
        usage_row["node_run_id"] = int(usage_row["node_run_id"])

    run_items: list[dict[str, Any]] = []
    total_message_count = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_tokens = 0
    total_cost_value = 0.0
    total_pricing_missing = False
    runs_with_usage = 0
    runs_without_usage = 0

    for run in runs:
        run_id = int(run["id"])
        node_key = str(run.get("node_key", ""))
        if run_id not in usage_by_run:
            run_items.append(
                {
                    "run_id": run_id,
                    "node_key": node_key,
                    "status": str(run.get("status", "")),
                    "usage": None,
                    "reason": _NO_USAGE_REASON,
                }
            )
            runs_without_usage += 1
            continue

        usage_row = usage_by_run[run_id]
        usage = _usage_dict(usage_row, config)
        run_items.append(
            {
                "run_id": run_id,
                "node_key": node_key,
                "status": str(run.get("status", "")),
                "usage": usage,
                "reason": None,
            }
        )
        runs_with_usage += 1
        total_message_count += int(usage_row.get("message_count", 0))
        total_input += int(usage_row.get("input_tokens", 0))
        total_output += int(usage_row.get("output_tokens", 0))
        total_cache_read += int(usage_row.get("cache_read_tokens", 0))
        total_tokens += int(usage_row.get("total_tokens", 0))
        cost = usage["cost"]
        total_cost_value += float(cost["total"])
        if cost.get("pricing_missing"):
            total_pricing_missing = True

    total_cost = _cost_breakdown(
        total_input,
        total_output,
        total_cache_read,
        total_tokens,
        "",
        "",
        config,
    )
    if runs_with_usage > 0:
        total_cost = {
            **total_cost,
            "total": total_cost_value,
            "pricing_missing": total_pricing_missing,
        }

    return {
        "job_id": job_id,
        "runs": run_items,
        "total": {
            "message_count": total_message_count,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "total_tokens": total_tokens,
            "cost": total_cost,
        },
        "runs_with_usage": runs_with_usage,
        "runs_without_usage": runs_without_usage,
        "currency": _currency_from_config(config),
    }


def _group_column(group_by: str) -> str:
    if group_by in {"node", "node_key"}:
        return "node_key"
    if group_by in {"provider", "model", "skill_version"}:
        return group_by
    return "node_key"


def _group_label(group_by: str, group_key: str) -> dict[str, str]:
    column = _group_column(group_by)
    labels = {"node_key": "", "provider": "", "model": "", "skill_version": ""}
    labels[column] = group_key
    return labels


def _query_workspace_usage_rows(
    job_db: Any,
    workspace_id: str,
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
    limit: int,
) -> Sequence[Mapping[str, Any]]:
    clauses = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if node_key:
        clauses.append("node_key = ?")
        params.append(node_key)
    if job_id:
        clauses.append("job_id = ?")
        params.append(job_id)
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if model:
        clauses.append("model = ?")
        params.append(model)
    if skill_version:
        clauses.append("skill_version = ?")
        params.append(skill_version)
    params.append(max(1, limit))
    where = " and ".join(clauses)
    with job_db._connect_read() as conn:
        rows = conn.execute(
            f"select * from node_run_token_usage where {where} order by node_run_id limit ?",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _count_workspace_runs(
    job_db: Any,
    workspace_id: str,
    *,
    node_key: str | None,
    job_id: str | None,
) -> int:
    clauses = ["jobs.workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if node_key:
        clauses.append("node_runs.node_key = ?")
        params.append(node_key)
    if job_id:
        clauses.append("node_runs.job_id = ?")
        params.append(job_id)
    where = " and ".join(clauses)
    with job_db._connect_read() as conn:
        row = conn.execute(
            f"select count(*) as count from node_runs join jobs on jobs.id = node_runs.job_id where {where}",
            params,
        ).fetchone()
    return int(row["count"]) if row else 0


def build_workspace_usage_response(
    job_db: Any,
    workspace_id: str,
    config: dict[str, Any],
    *,
    node_key: str | None = None,
    job_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    skill_version: str | None = None,
    group_by: str = "node",
    limit: int = 100,
) -> dict[str, Any]:
    rows = _query_workspace_usage_rows(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
        limit=limit,
    )
    total_runs = _count_workspace_runs(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
    )
    group_column = _group_column(group_by)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row[group_column])
        grouped.setdefault(key, []).append(dict(row))

    summary_message_count = 0
    summary_input = 0
    summary_output = 0
    summary_cache_read = 0
    summary_tokens = 0
    summary_cost = 0.0
    summary_pricing_missing = False

    groups: list[dict[str, Any]] = []
    for group_key, group_rows in grouped.items():
        runs = len(group_rows)
        total_input = sum(int(r.get("input_tokens", 0)) for r in group_rows)
        total_output = sum(int(r.get("output_tokens", 0)) for r in group_rows)
        total_cache_read = sum(int(r.get("cache_read_tokens", 0)) for r in group_rows)
        total_tokens = sum(int(r.get("total_tokens", 0)) for r in group_rows)
        message_count = sum(int(r.get("message_count", 0)) for r in group_rows)

        group_cost = 0.0
        group_pricing_missing = False
        for r in group_rows:
            cost = calculate_cost(
                int(r.get("total_tokens", 0)),
                int(r.get("input_tokens", 0)),
                int(r.get("output_tokens", 0)),
                int(r.get("cache_read_tokens", 0)),
                str(r.get("provider", "")),
                str(r.get("model", "")),
                config,
            )
            group_cost += cost.total
            if cost.pricing_missing:
                group_pricing_missing = True

        labels = _group_label(group_by, group_key)
        coverage = runs / total_runs if total_runs > 0 else 0.0
        groups.append(
            {
                "group_key": group_key,
                "node_key": labels["node_key"],
                "provider": labels["provider"],
                "model": labels["model"],
                "skill_version": labels["skill_version"],
                "runs": runs,
                "avg_input_tokens": total_input / runs if runs else 0.0,
                "avg_output_tokens": total_output / runs if runs else 0.0,
                "avg_cache_read_tokens": total_cache_read / runs if runs else 0.0,
                "avg_total_tokens": total_tokens / runs if runs else 0.0,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_cache_read_tokens": total_cache_read,
                "total_tokens": total_tokens,
                "total_cost": group_cost,
                "avg_cost": group_cost / runs if runs else 0.0,
                "pricing_missing": group_pricing_missing,
                "coverage": coverage,
            }
        )

        summary_message_count += message_count
        summary_input += total_input
        summary_output += total_output
        summary_cache_read += total_cache_read
        summary_tokens += total_tokens
        summary_cost += group_cost
        if group_pricing_missing:
            summary_pricing_missing = True

    runs_with_usage = len(rows)
    runs_without_usage = max(0, total_runs - runs_with_usage)

    summary_cost_breakdown = _cost_breakdown(
        summary_input,
        summary_output,
        summary_cache_read,
        summary_tokens,
        "",
        "",
        config,
    )
    if runs_with_usage > 0:
        summary_cost_breakdown = {
            **summary_cost_breakdown,
            "total": summary_cost,
            "pricing_missing": summary_pricing_missing,
        }

    return {
        "workspace_id": workspace_id,
        "currency": _currency_from_config(config),
        "summary": {
            "message_count": summary_message_count,
            "input_tokens": summary_input,
            "output_tokens": summary_output,
            "cache_read_tokens": summary_cache_read,
            "total_tokens": summary_tokens,
            "cost": summary_cost_breakdown,
        },
        "groups": groups,
        "runs_with_usage": runs_with_usage,
        "runs_without_usage": runs_without_usage,
    }
