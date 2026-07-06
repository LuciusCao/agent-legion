from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.services.token_usage_pricing import calculate_cost, load_pricing_config
from server.app.services.token_usage_response import (
    _currency_from_config,
    build_aggregate_cost,
    usage_dict,
)
from server.app.storage_paths import ManagedPathError, resolve_data_path

logger = logging.getLogger(__name__)

DEFAULT_USAGE_SOURCE = "events_jsonl"


@dataclass(frozen=True)
class TokenUsageSummary:
    node_run_id: int
    job_id: str
    workspace_id: str
    node_key: str
    provider: str
    model: str
    skill_version: str
    message_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_tokens: int
    usage_source: str = DEFAULT_USAGE_SOURCE
    is_complete: bool = True
    parse_error: str = ""


def _read_run_json(run_dir: Path) -> dict[str, Any]:
    run_json_path = run_dir / "run.json"
    if not run_json_path.is_file():
        return {}
    try:
        data = json.loads(run_json_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_usage(event: dict[str, Any]) -> dict[str, int] | None:
    if event.get("type") != "message_end":
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        input_tokens = int(usage.get("input", 0))
        output_tokens = int(usage.get("output", 0))
        cache_read_tokens = int(usage.get("cacheRead", 0))
    except (TypeError, ValueError):
        return None
    return {
        "input": max(0, input_tokens),
        "output": max(0, output_tokens),
        "cache_read": max(0, cache_read_tokens),
    }


def _provider_model_from_run_json(run_json: dict[str, Any]) -> tuple[str, str]:
    model = run_json.get("model") or {}
    if isinstance(model, dict):
        provider = str(model.get("provider", "")).strip()
        model_name = str(model.get("model", "")).strip()
        return provider, model_name
    return "", ""


def parse_run_usage(
    run_dir: Path,
    node_run: Mapping[str, Any],
    workspace_id: str | None = None,
) -> TokenUsageSummary | None:
    """Parse token usage for a single node run.

    Returns ``None`` when ``events.jsonl`` is missing. Malformed lines are
    ignored; if at least one valid usage event is found the summary is marked
    ``is_complete=False`` and ``parse_error`` is populated.
    """
    events_path = run_dir / "events.jsonl"
    if not events_path.is_file():
        return None

    run_json = _read_run_json(run_dir)
    provider, model = _provider_model_from_run_json(run_json)
    provider_from_run_json = bool(provider)
    model_from_run_json = bool(model)

    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    message_count = 0
    parse_errors: list[str] = []

    try:
        with events_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    parse_errors.append(f"json decode error: {exc}")
                    continue
                if not isinstance(event, dict):
                    continue
                usage = _extract_usage(event)
                if usage is None:
                    continue
                input_tokens += usage["input"]
                output_tokens += usage["output"]
                cache_read_tokens += usage["cache_read"]
                message_count += 1
                # Latest message_end wins for provider/model fallback.
                message = event.get("message") or {}
                if isinstance(message, dict):
                    event_provider = str(message.get("provider", "")).strip()
                    if event_provider and not provider_from_run_json:
                        provider = event_provider
                    event_model = str(message.get("model", "")).strip()
                    if event_model and not model_from_run_json:
                        model = event_model
    except OSError as exc:
        parse_errors.append(f"failed to read events.jsonl: {exc}")

    if message_count == 0:
        if parse_errors:
            # Malformed events file with no valid usage: do not persist a row.
            return None
        # Empty events file with no usage events: do not persist a row.
        return None

    skill_version = str(node_run.get("skill_version", "")).strip()
    if not skill_version:
        skill_version = str(run_json.get("skill_version", "")).strip()

    is_complete = not parse_errors
    parse_error = "; ".join(parse_errors) if parse_errors else ""

    node_run_id = int(node_run.get("id") or node_run.get("node_run_id") or 0)
    return TokenUsageSummary(
        node_run_id=node_run_id,
        job_id=str(node_run.get("job_id", "")),
        workspace_id=str(workspace_id or node_run.get("workspace_id", "")),
        node_key=str(node_run.get("node_key", "")),
        provider=provider,
        model=model,
        skill_version=skill_version,
        message_count=message_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        total_tokens=input_tokens + output_tokens + cache_read_tokens,
        usage_source=DEFAULT_USAGE_SOURCE,
        is_complete=is_complete,
        parse_error=parse_error,
    )


def persist_node_run_usage(conn: sqlite3.Connection, summary: TokenUsageSummary) -> None:
    """Persist a token usage summary, replacing any existing row for the run."""
    conn.execute(
        """
        insert into node_run_token_usage(
          node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
          message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens,
          usage_source, is_complete, parse_error, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
        on conflict(node_run_id) do update set
          job_id=excluded.job_id,
          workspace_id=excluded.workspace_id,
          node_key=excluded.node_key,
          provider=excluded.provider,
          model=excluded.model,
          skill_version=excluded.skill_version,
          message_count=excluded.message_count,
          input_tokens=excluded.input_tokens,
          output_tokens=excluded.output_tokens,
          cache_read_tokens=excluded.cache_read_tokens,
          total_tokens=excluded.total_tokens,
          usage_source=excluded.usage_source,
          is_complete=excluded.is_complete,
          parse_error=excluded.parse_error,
          updated_at=current_timestamp
        """,
        (
            summary.node_run_id,
            summary.job_id,
            summary.workspace_id,
            summary.node_key,
            summary.provider,
            summary.model,
            summary.skill_version,
            summary.message_count,
            summary.input_tokens,
            summary.output_tokens,
            summary.cache_read_tokens,
            summary.total_tokens,
            summary.usage_source,
            1 if summary.is_complete else 0,
            summary.parse_error,
        ),
    )


def backfill_missing_token_usage(conn: sqlite3.Connection, data_dir: Path, limit: int = 500) -> int:
    """Backfill token usage for completed/failed node runs that lack a summary row.

    Returns the number of rows persisted. Missing run directories and missing
    ``events.jsonl`` are skipped silently because they are normal for cleaned-up
    historical runs.
    """
    rows = conn.execute(
        """
        select
          nr.id as node_run_id,
          nr.job_id,
          nr.node_key,
          nr.run_dir,
          nr.skill_version,
          j.workspace_id
        from node_runs as nr
        join jobs as j on j.id = nr.job_id
        where not exists (
          select 1 from node_run_token_usage where node_run_id = nr.id
        )
          and nr.status in ('completed', 'failed')
          and nr.run_dir != ''
        order by nr.id
        limit ?
        """,
        (max(1, limit),),
    ).fetchall()

    persisted = 0
    for row in rows:
        try:
            run_dir = resolve_data_path(row["run_dir"], data_dir, allow_missing=False)
        except (ManagedPathError, FileNotFoundError):
            continue

        node_run = dict(row)
        summary = parse_run_usage(run_dir, node_run)
        if summary is None:
            continue
        summary = TokenUsageSummary(
            **{**summary.__dict__, "workspace_id": str(row["workspace_id"])}
        )
        try:
            persist_node_run_usage(conn, summary)
            persisted += 1
        except sqlite3.Error:
            logger.exception("Failed to persist token usage for run %s", row["node_run_id"])

    return persisted


_NO_USAGE_REASON = "no token usage recorded for run"


def build_run_usage_response(
    job_db: Any,
    run: Mapping[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    run_id = int(run["id"])
    job_id = str(run["job_id"])
    with job_db.connect() as conn:
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
        "usage": usage_dict(dict(row), config),
        "reason": None,
    }


def build_job_usage_response(
    job_db: Any,
    job_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    runs = job_db.list_node_runs(job_id)
    with job_db.connect() as conn:
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
        usage = usage_dict(usage_row, config)
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
        if cost is not None:
            total_cost_value += float(cost["total"])
        if usage["pricing_missing"]:
            total_pricing_missing = True

    total_cost_obj = build_aggregate_cost(
        total_input,
        total_output,
        total_cache_read,
        total_tokens,
        total_cost_value,
        total_pricing_missing,
        list(usage_by_run.values()) if runs_with_usage > 0 else [],
        config,
    )

    return {
        "job_id": job_id,
        "runs": run_items,
        "total": {
            "message_count": total_message_count,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "total_tokens": total_tokens,
            "cost": total_cost_obj["cost"],
            "pricing_missing": total_cost_obj["pricing_missing"],
        },
        "runs_with_usage": runs_with_usage,
        "runs_without_usage": runs_without_usage,
        "currency": _currency_from_config(config),
    }


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


def _workspace_usage_filter_clauses(
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
) -> tuple[list[str], list[Any]]:
    """Return (clauses, params) for node_run_token_usage filter queries."""
    clauses: list[str] = []
    params: list[Any] = []
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
    return clauses, params


def _query_workspace_usage_aggregates(
    job_db: Any,
    workspace_id: str,
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
) -> dict[str, Any]:
    """Aggregate token usage over the full filtered set (not limited)."""
    filter_clauses, filter_params = _workspace_usage_filter_clauses(
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    clauses = ["workspace_id = ?", *filter_clauses]
    params: list[Any] = [workspace_id, *filter_params]
    where = " and ".join(clauses)
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
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
    group_by: str,
    limit: int,
) -> Sequence[Mapping[str, Any]]:
    """Aggregate per group over the full filtered set, then apply limit."""
    group_expr, group_columns = _group_by_sql(group_by)
    filter_clauses, filter_params = _workspace_usage_filter_clauses(
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    clauses = ["workspace_id = ?", *filter_clauses]
    params: list[Any] = [workspace_id, *filter_params]
    where = " and ".join(clauses)
    group_by_sql = ", ".join(group_columns)
    params.append(max(1, limit))
    with job_db.connect() as conn:
        rows = conn.execute(
            f"""
            select
              {group_expr} as group_key,
              node_key,
              provider,
              model,
              skill_version,
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
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
    group_by: str,
) -> Sequence[Mapping[str, Any]]:
    """Aggregate token and cost inputs per (group_key, provider, model).

    This is separate from the displayed group aggregates because a single
    displayed group (e.g. one node) may contain runs under different
    provider/model pairs, and each pair must be priced independently.
    """
    group_expr, group_columns = _group_by_sql(group_by)
    filter_clauses, filter_params = _workspace_usage_filter_clauses(
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    clauses = ["workspace_id = ?", *filter_clauses]
    params: list[Any] = [workspace_id, *filter_params]
    where = " and ".join(clauses)
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
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
) -> Sequence[Mapping[str, Any]]:
    """Aggregate token and cost inputs per (provider, model) over the full set."""
    filter_clauses, filter_params = _workspace_usage_filter_clauses(
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    clauses = ["workspace_id = ?", *filter_clauses]
    params: list[Any] = [workspace_id, *filter_params]
    where = " and ".join(clauses)
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
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
) -> int:
    """Count runs in the matching dimension set.

    When provider/model/skill_version filters are present, runs that have a
    token usage row under a different provider/model/version are excluded from
    the denominator. The remaining count is the union of runs that lack any
    usage row and runs whose usage row matches the active filters.
    """
    clauses = ["jobs.workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if node_key:
        clauses.append("node_runs.node_key = ?")
        params.append(node_key)
    if job_id:
        clauses.append("node_runs.job_id = ?")
        params.append(job_id)

    usage_filter_clauses: list[str] = []
    if provider is not None:
        usage_filter_clauses.append("(u.provider != ? or u.provider is null)")
        params.append(provider)
    if model is not None:
        usage_filter_clauses.append("(u.model != ? or u.model is null)")
        params.append(model)
    if skill_version is not None:
        usage_filter_clauses.append("(u.skill_version != ? or u.skill_version is null)")
        params.append(skill_version)

    if usage_filter_clauses:
        # Exclude runs whose existing usage row does not match the active
        # filters. Runs without any usage row are still included.
        excluded = " or ".join(usage_filter_clauses)
        clauses.append(
            f"not exists (select 1 from node_run_token_usage u where u.node_run_id = node_runs.id and ({excluded}))"
        )

    where = " and ".join(clauses)
    with job_db.connect() as conn:
        row = conn.execute(
            f"select count(*) as count from node_runs join jobs on jobs.id = node_runs.job_id where {where}",
            params,
        ).fetchone()
    return int(row["count"]) if row else 0


def _count_runs_per_node_group(
    job_db: Any,
    workspace_id: str,
    *,
    node_key: str | None,
    job_id: str | None,
    provider: str | None,
    model: str | None,
    skill_version: str | None,
) -> dict[str, int]:
    """Count total runs per node_key for coverage denominator."""
    clauses = ["jobs.workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if node_key:
        clauses.append("node_runs.node_key = ?")
        params.append(node_key)
    if job_id:
        clauses.append("node_runs.job_id = ?")
        params.append(job_id)

    usage_filter_clauses: list[str] = []
    if provider is not None:
        usage_filter_clauses.append("(u.provider != ? or u.provider is null)")
        params.append(provider)
    if model is not None:
        usage_filter_clauses.append("(u.model != ? or u.model is null)")
        params.append(model)
    if skill_version is not None:
        usage_filter_clauses.append("(u.skill_version != ? or u.skill_version is null)")
        params.append(skill_version)

    if usage_filter_clauses:
        excluded = " or ".join(usage_filter_clauses)
        clauses.append(
            f"not exists (select 1 from node_run_token_usage u where u.node_run_id = node_runs.id and ({excluded}))"
        )

    where = " and ".join(clauses)
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
    aggregates = _query_workspace_usage_aggregates(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    group_rows = _query_workspace_group_aggregates(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
        group_by=group_by,
        limit=limit,
    )
    group_cost_rows = _query_workspace_group_cost_rows(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
        group_by=group_by,
    )
    summary_cost_rows = _query_workspace_summary_cost_rows(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    total_runs = _count_workspace_runs(
        job_db,
        workspace_id,
        node_key=node_key,
        job_id=job_id,
        provider=provider,
        model=model,
        skill_version=skill_version,
    )
    node_group_totals: dict[str, int] = {}
    if group_by == "node":
        node_group_totals = _count_runs_per_node_group(
            job_db,
            workspace_id,
            node_key=node_key,
            job_id=job_id,
            provider=provider,
            model=model,
            skill_version=skill_version,
        )

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
        "currency": _currency_from_config(config),
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


__all__ = [
    "TokenUsageSummary",
    "parse_run_usage",
    "persist_node_run_usage",
    "backfill_missing_token_usage",
    "calculate_cost",
    "load_pricing_config",
    "build_run_usage_response",
    "build_job_usage_response",
    "build_workspace_usage_response",
]
