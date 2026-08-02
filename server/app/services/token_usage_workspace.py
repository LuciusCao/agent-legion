from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from server.app.services.token_usage_pricing import calculate_cost
from server.app.services.token_usage_response import (
    build_aggregate_cost,
    currency_from_config,
)


def _group_column(group_by: str) -> str:
    if group_by in {"node", "node_key", "node_skill_version"}:
        return "node_key"
    if group_by in {"provider", "model", "skill_version"}:
        return group_by
    return "node_key"


def _group_label(group_by: str, group_key: str) -> dict[str, str]:
    if group_by == "node_skill_version":
        node_key, _, skill_version = group_key.partition(" / ")
        return {"node_key": node_key, "provider": "", "model": "", "skill_version": skill_version}
    column = _group_column(group_by)
    labels = {"node_key": "", "provider": "", "model": "", "skill_version": ""}
    labels[column] = group_key
    return labels


@dataclass(frozen=True)
class _UsageFilters:
    """Optional dimension filters shared by the workspace usage queries."""

    node_key: str | None = None
    job_id: str | None = None
    provider: str | None = None
    model: str | None = None
    skill_version: str | None = None

    def usage_where(self, workspace_id: str) -> tuple[str, list[Any]]:
        """Return (where_sql, params) for node_run_token_usage filter queries."""
        clauses = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if self.node_key:
            clauses.append("node_key = ?")
            params.append(self.node_key)
        if self.job_id:
            clauses.append("job_id = ?")
            params.append(self.job_id)
        if self.provider:
            clauses.append("provider = ?")
            params.append(self.provider)
        if self.model:
            clauses.append("model = ?")
            params.append(self.model)
        if self.skill_version:
            clauses.append("skill_version = ?")
            params.append(self.skill_version)
        return " and ".join(clauses), params

    def run_count_where(self, workspace_id: str) -> tuple[str, list[Any]]:
        """Return (where_sql, params) for node_runs/jobs count queries.

        When provider/model/skill_version filters are present, runs that have
        a token usage row under a different provider/model/version are
        excluded from the count. Runs without any usage row are still
        included.
        """
        clauses = ["jobs.workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if self.node_key:
            clauses.append("node_runs.node_key = ?")
            params.append(self.node_key)
        if self.job_id:
            clauses.append("node_runs.job_id = ?")
            params.append(self.job_id)

        usage_filter_clauses: list[str] = []
        if self.provider is not None:
            usage_filter_clauses.append("(u.provider != ? or u.provider is null)")
            params.append(self.provider)
        if self.model is not None:
            usage_filter_clauses.append("(u.model != ? or u.model is null)")
            params.append(self.model)
        if self.skill_version is not None:
            usage_filter_clauses.append("(u.skill_version != ? or u.skill_version is null)")
            params.append(self.skill_version)

        if usage_filter_clauses:
            # Exclude runs whose existing usage row does not match the active
            # filters. Runs without any usage row are still included.
            excluded = " or ".join(usage_filter_clauses)
            clauses.append(
                "not exists (select 1 from node_run_token_usage u "
                f"where u.node_run_id = node_runs.id and ({excluded}))"
            )
        return " and ".join(clauses), params


def _query_workspace_usage_aggregates(
    job_db: Any,
    workspace_id: str,
    filters: _UsageFilters,
) -> dict[str, Any]:
    """Aggregate token usage over the full filtered set (not limited)."""
    where, params = filters.usage_where(workspace_id)
    with job_db.connect() as conn:
        row = conn.execute(
            f"""
            select
              coalesce(sum(message_count), 0) as message_count,
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              count(*) as runs_with_usage
            from node_run_token_usage
            where {where}
            """,
            params,
        ).fetchone()
    return (
        dict(row)
        if row
        else {
            "message_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
            "runs_with_usage": 0,
        }
    )


def _group_by_sql(group_by: str) -> tuple[str, list[str]]:
    """Return (select/group_by expression, group columns) for SQL aggregation."""
    if group_by == "node_skill_version":
        return "node_key || ' / ' || skill_version", ["node_key", "skill_version"]
    column = _group_column(group_by)
    return column, [column]


def _query_workspace_group_aggregates(
    job_db: Any,
    workspace_id: str,
    filters: _UsageFilters,
    *,
    group_by: str,
    limit: int,
) -> Sequence[Mapping[str, Any]]:
    """Aggregate per group over the full filtered set, then apply limit."""
    group_expr, group_columns = _group_by_sql(group_by)
    where, params = filters.usage_where(workspace_id)
    group_by_sql = ", ".join(group_columns)
    params.append(max(1, limit))
    with job_db.connect() as conn:
        rows = conn.execute(
            f"""
            select
              {group_expr} as group_key,
              count(*) as runs,
              sum(input_tokens) as total_input_tokens,
              sum(output_tokens) as total_output_tokens,
              sum(cache_read_tokens) as total_cache_read_tokens,
              sum(total_tokens) as total_tokens,
              sum(message_count) as message_count,
              avg(input_tokens) as avg_input_tokens,
              avg(output_tokens) as avg_output_tokens,
              avg(cache_read_tokens) as avg_cache_read_tokens,
              avg(total_tokens) as avg_total_tokens
            from node_run_token_usage
            where {where}
            group by {group_by_sql}
            order by total_tokens desc
            limit ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _query_workspace_group_cost_rows(
    job_db: Any,
    workspace_id: str,
    filters: _UsageFilters,
    *,
    group_by: str,
) -> Sequence[Mapping[str, Any]]:
    """Aggregate token and cost inputs per (group_key, provider, model).

    This is separate from the displayed group aggregates because a single
    displayed group (e.g. one node) may contain runs under different
    provider/model pairs, and each pair must be priced independently.
    """
    group_expr, group_columns = _group_by_sql(group_by)
    where, params = filters.usage_where(workspace_id)
    group_by_sql = ", ".join([*group_columns, "provider", "model"])
    with job_db.connect() as conn:
        rows = conn.execute(
            f"""
            select
              {group_expr} as group_key,
              provider,
              model,
              sum(input_tokens) as total_input_tokens,
              sum(output_tokens) as total_output_tokens,
              sum(cache_read_tokens) as total_cache_read_tokens,
              sum(total_tokens) as total_tokens
            from node_run_token_usage
            where {where}
            group by {group_by_sql}
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _query_workspace_summary_cost_rows(
    job_db: Any,
    workspace_id: str,
    filters: _UsageFilters,
) -> Sequence[Mapping[str, Any]]:
    """Aggregate token and cost inputs per (provider, model) over the full set."""
    where, params = filters.usage_where(workspace_id)
    with job_db.connect() as conn:
        rows = conn.execute(
            f"""
            select
              provider,
              model,
              sum(input_tokens) as total_input_tokens,
              sum(output_tokens) as total_output_tokens,
              sum(cache_read_tokens) as total_cache_read_tokens,
              sum(total_tokens) as total_tokens
            from node_run_token_usage
            where {where}
            group by provider, model
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _count_workspace_runs(
    job_db: Any,
    workspace_id: str,
    filters: _UsageFilters,
) -> int:
    """Count runs in the matching dimension set.

    See ``_UsageFilters.run_count_where`` for how provider/model/skill_version
    filters affect the denominator.
    """
    where, params = filters.run_count_where(workspace_id)
    with job_db.connect() as conn:
        row = conn.execute(
            f"select count(*) as count from node_runs join jobs on jobs.id = node_runs.job_id where {where}",
            params,
        ).fetchone()
    return int(row["count"]) if row else 0


def _count_runs_per_node_group(
    job_db: Any,
    workspace_id: str,
    filters: _UsageFilters,
) -> dict[str, int]:
    """Count total runs per node_key for coverage denominator."""
    where, params = filters.run_count_where(workspace_id)
    with job_db.connect() as conn:
        rows = conn.execute(
            f"""
            select node_runs.node_key as group_key, count(*) as count
            from node_runs join jobs on jobs.id = node_runs.job_id
            where {where}
            group by node_runs.node_key
            """,
            params,
        ).fetchall()
    return {str(row["group_key"]): int(row["count"]) for row in rows}


def _sum_cost_rows(
    rows: Sequence[Mapping[str, Any]],
    config: dict[str, Any],
) -> tuple[float | None, bool]:
    """Return (total_cost, pricing_missing) by pricing each row's provider/model.

    Rows are expected to carry aggregate columns ``total_input_tokens``,
    ``total_output_tokens``, ``total_cache_read_tokens`` and ``total_tokens``.
    ``total_cost`` is ``None`` when every row lacks configured pricing.
    """
    total_cost: float | None = None
    pricing_missing = False
    for r in rows:
        cost = calculate_cost(
            int(r.get("total_tokens", 0)),
            int(r.get("total_input_tokens", 0)),
            int(r.get("total_output_tokens", 0)),
            int(r.get("total_cache_read_tokens", 0)),
            str(r.get("provider", "")),
            str(r.get("model", "")),
            config,
        )
        if cost is None:
            pricing_missing = True
            continue
        total_cost = (total_cost or 0.0) + cost.total
    return total_cost, pricing_missing


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
    filters = _UsageFilters(
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    aggregates = _query_workspace_usage_aggregates(job_db, workspace_id, filters)
    group_rows = _query_workspace_group_aggregates(
        job_db,
        workspace_id,
        filters,
        group_by=group_by,
        limit=limit,
    )
    group_cost_rows = _query_workspace_group_cost_rows(
        job_db,
        workspace_id,
        filters,
        group_by=group_by,
    )
    summary_cost_rows = _query_workspace_summary_cost_rows(job_db, workspace_id, filters)
    total_runs = _count_workspace_runs(job_db, workspace_id, filters)
    node_group_totals: dict[str, int] = {}
    if group_by == "node":
        node_group_totals = _count_runs_per_node_group(job_db, workspace_id, filters)

    group_cost_by_key: dict[str, list[dict[str, Any]]] = {}
    for r in group_cost_rows:
        group_cost_by_key.setdefault(str(r["group_key"]), []).append(dict(r))

    groups: list[dict[str, Any]] = []

    for row in group_rows:
        group_key = str(row["group_key"])
        runs = int(row["runs"])
        total_input = int(row["total_input_tokens"])
        total_output = int(row["total_output_tokens"])
        total_cache_read = int(row["total_cache_read_tokens"])
        total_tokens = int(row["total_tokens"])
        group_cost, group_pricing_missing = _sum_cost_rows(
            group_cost_by_key.get(group_key, []), config
        )

        # Model/skill_version groups are defined only by usage rows, so the
        # denominator is the number of usage rows in the group.
        group_total_runs = node_group_totals.get(group_key, 0) if group_by == "node" else runs
        coverage = runs / group_total_runs if group_total_runs > 0 else 0.0

        labels = _group_label(group_by, group_key)
        groups.append(
            {
                "group_key": group_key,
                "node_key": labels["node_key"],
                "provider": labels["provider"],
                "model": labels["model"],
                "skill_version": labels["skill_version"],
                "runs": runs,
                "avg_input_tokens": float(row["avg_input_tokens"] or 0),
                "avg_output_tokens": float(row["avg_output_tokens"] or 0),
                "avg_cache_read_tokens": float(row["avg_cache_read_tokens"] or 0),
                "avg_total_tokens": float(row["avg_total_tokens"] or 0),
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_cache_read_tokens": total_cache_read,
                "total_tokens": total_tokens,
                "total_cost": group_cost,
                "avg_cost": group_cost / runs if group_cost is not None and runs else None,
                "pricing_missing": group_pricing_missing,
                "coverage": coverage,
            }
        )

    runs_with_usage = int(aggregates["runs_with_usage"])
    runs_without_usage = max(0, total_runs - runs_with_usage)

    summary_cost, summary_pricing_missing = _sum_cost_rows(summary_cost_rows, config)
    summary_cost_obj = build_aggregate_cost(
        int(aggregates["input_tokens"]),
        int(aggregates["output_tokens"]),
        int(aggregates["cache_read_tokens"]),
        int(aggregates["total_tokens"]),
        summary_cost,
        summary_pricing_missing,
        summary_cost_rows,
        config,
    )

    return {
        "workspace_id": workspace_id,
        "currency": currency_from_config(config),
        "summary": {
            "message_count": int(aggregates["message_count"]),
            "input_tokens": int(aggregates["input_tokens"]),
            "output_tokens": int(aggregates["output_tokens"]),
            "cache_read_tokens": int(aggregates["cache_read_tokens"]),
            "total_tokens": int(aggregates["total_tokens"]),
            "cost": summary_cost_obj["cost"],
            "pricing_missing": summary_cost_obj["pricing_missing"],
        },
        "groups": groups,
        "runs_with_usage": runs_with_usage,
        "runs_without_usage": runs_without_usage,
    }


__all__ = ["build_workspace_usage_response"]
