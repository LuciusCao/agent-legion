from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.services.token_usage_pricing import calculate_cost, load_pricing_config
from server.app.services.token_usage_response import (
    build_aggregate_cost,
    currency_from_config,
    usage_dict,
)
from server.app.services.token_usage_workspace import build_workspace_usage_response

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


def persist_node_run_usage(conn: DatabaseConnection, summary: TokenUsageSummary) -> None:
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
        "currency": currency_from_config(config),
    }


__all__ = [
    "TokenUsageSummary",
    "parse_run_usage",
    "persist_node_run_usage",
    "calculate_cost",
    "load_pricing_config",
    "build_run_usage_response",
    "build_job_usage_response",
    "build_workspace_usage_response",
]
