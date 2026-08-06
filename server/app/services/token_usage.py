from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.services.token_usage_parse import TokenUsageSummary
from server.app.services.token_usage_pricing import calculate_cost, load_pricing_config
from server.app.services.token_usage_response import (
    build_aggregate_cost,
    currency_from_config,
    usage_dict,
)
from server.app.services.token_usage_workspace import build_workspace_usage_response

logger = logging.getLogger(__name__)


def persist_node_run_usage(conn: DatabaseConnection, summary: TokenUsageSummary) -> None:
    """Persist a token usage summary, replacing any existing row for the run."""
    conn.execute(
        """
        insert into node_run_token_usage(
          node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
          message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens,
          usage_source, is_complete, parse_error, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, current_timestamp)
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
            "select * from node_run_token_usage where node_run_id=%s",
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
            "select * from node_run_token_usage where job_id=%s",
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
    total_cost_value: float | None = None
    total_pricing_missing_models: set[str] = set()
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
            total_cost_value = (total_cost_value or 0.0) + float(cost["total"])
        if usage["pricing_missing"]:
            total_pricing_missing_models.add(f"{usage['provider']}/{usage['model']}")

    total_cost_obj = build_aggregate_cost(
        total_input,
        total_output,
        total_cache_read,
        total_tokens,
        total_cost_value,
        sorted(total_pricing_missing_models),
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
            "pricing_missing_models": total_cost_obj["pricing_missing_models"],
        },
        "runs_with_usage": runs_with_usage,
        "runs_without_usage": runs_without_usage,
        "currency": currency_from_config(config),
    }


__all__ = [
    "persist_node_run_usage",
    "calculate_cost",
    "load_pricing_config",
    "build_run_usage_response",
    "build_job_usage_response",
    "build_workspace_usage_response",
]
