from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.services.token_usage_contracts import CostBreakdown
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


def load_pricing_config(config: dict[str, Any]) -> dict[tuple[str, str], dict[str, float]]:
    token_usage = config.get("token_usage", {})
    pricing = token_usage.get("pricing", [])
    return {
        (str(p["provider"]).strip(), str(p["model"]).strip()): {
            "input_per_1m": float(p["input_per_1m"]),
            "output_per_1m": float(p["output_per_1m"]),
            "cache_read_per_1m": float(p["cache_read_per_1m"]),
        }
        for p in pricing
        if "provider" in p and "model" in p
    }


def calculate_cost(
    total_tokens: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    provider: str,
    model: str,
    pricing_config: dict[str, Any],
) -> CostBreakdown:
    """Return a cost breakdown for the given usage and provider/model."""
    pricing = load_pricing_config(pricing_config)
    rates = pricing.get((provider.strip(), model.strip()))
    currency = str(pricing_config.get("token_usage", {}).get("currency", "")).strip()

    if rates is None:
        return CostBreakdown(
            currency=currency,
            input=0.0,
            output=0.0,
            cache_read=0.0,
            total=0.0,
            pricing_missing=True,
        )

    input_cost = input_tokens * rates["input_per_1m"] / 1_000_000
    output_cost = output_tokens * rates["output_per_1m"] / 1_000_000
    cache_read_cost = cache_read_tokens * rates["cache_read_per_1m"] / 1_000_000
    return CostBreakdown(
        currency=currency,
        input=input_cost,
        output=output_cost,
        cache_read=cache_read_cost,
        total=input_cost + output_cost + cache_read_cost,
        pricing_missing=False,
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
          node_runs.id as node_run_id,
          node_runs.job_id,
          node_runs.node_key,
          node_runs.run_dir,
          node_runs.skill_version,
          jobs.workspace_id
        from node_runs
        left join node_run_token_usage on node_run_token_usage.node_run_id = node_runs.id
        join jobs on jobs.id = node_runs.job_id
        where node_run_token_usage.id is null
          and node_runs.status in ('completed', 'failed')
        order by node_runs.id
        limit ?
        """,
        (max(1, limit),),
    ).fetchall()

    persisted = 0
    for row in rows:
        run_dir_value = row["run_dir"]
        if not run_dir_value:
            continue
        try:
            run_dir = resolve_data_path(run_dir_value, data_dir, allow_missing=False)
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
