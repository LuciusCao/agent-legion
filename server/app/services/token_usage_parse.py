"""Token usage parsing: read ``events.jsonl`` / ``run.json`` into a summary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
