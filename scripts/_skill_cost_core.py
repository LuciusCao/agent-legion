"""Shared helpers for comparing skill execution costs."""

from __future__ import annotations

import fnmatch
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

DEFAULT_PRICING = {
    "model": "Doubao-Seed-2.1-turbo",
    "input_per_1m": 3.0,
    "output_per_1m": 15.0,
    "cache_read_per_1m": 0.6,
}
ZERO_STATS = {"runs": 0, "messages": 0, "input": 0, "output": 0, "cache_read": 0}


def load_pricing(path: Path | None) -> dict[str, float | str]:
    if path is None:
        return DEFAULT_PRICING.copy()  # type: ignore[return-value]
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "model": data.get("model", DEFAULT_PRICING["model"]),
        "input_per_1m": float(data.get("input_per_1m", DEFAULT_PRICING["input_per_1m"])),
        "output_per_1m": float(data.get("output_per_1m", DEFAULT_PRICING["output_per_1m"])),
        "cache_read_per_1m": float(
            data.get("cache_read_per_1m", DEFAULT_PRICING["cache_read_per_1m"])
        ),
    }


def resolve_job_paths(source: str) -> list[Path]:
    raw = source.strip()
    if not raw:
        return []
    candidate = Path(raw)
    if candidate.is_file():
        paths: list[Path] = []
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                p = Path(line)
                if p.is_dir():
                    paths.append(p)
        return paths
    if "*" in raw or "?" in raw:
        return [
            Path(p)
            for p in fnmatch.filter([str(p) for p in Path().rglob("*")], raw)
            if Path(p).is_dir()
        ]
    return [candidate] if candidate.is_dir() else []


def _load_run_meta(run_dir: Path) -> dict[str, Any]:
    run_json = run_dir / "run.json"
    if run_json.is_file():
        try:
            return cast(dict[str, Any], json.loads(run_json.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return {}


def aggregate_job(
    job_dir: Path, node_filter: str, version_filters: list[str]
) -> dict[str, Any] | None:
    runs_dir = job_dir / "runs"
    if not runs_dir.is_dir():
        return None
    node_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict(ZERO_STATS))
    for node_dir in runs_dir.iterdir():
        if not node_dir.is_dir():
            continue
        node_name = node_dir.name
        if node_filter and node_filter not in node_name:
            continue
        for run_dir in node_dir.iterdir():
            if not run_dir.is_dir():
                continue
            skill_version = _load_run_meta(run_dir).get("skill_version") or "unknown"
            if version_filters and not any(f in skill_version for f in version_filters):
                continue
            events_file = run_dir / "events.jsonl"
            if not events_file.is_file():
                continue
            node = node_stats[(node_name, skill_version)]
            node["runs"] += 1
            with events_file.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "message_end":
                        continue
                    msg = event.get("message", {})
                    usage = msg.get("usage") if isinstance(msg, dict) else None
                    if not usage:
                        continue
                    node["messages"] += 1
                    node["input"] += int(usage.get("input", 0) or 0)
                    node["output"] += int(usage.get("output", 0) or 0)
                    node["cache_read"] += int(usage.get("cacheRead", 0) or 0)
    if not node_stats:
        return None
    total = {k: sum(s[k] for s in node_stats.values()) for k in ZERO_STATS}
    return {"job_dir": job_dir, "nodes": dict(node_stats), "total": total}


def cost(tokens: int, per_1m: float) -> float:
    return tokens * per_1m / 1_000_000


def compute_costs(total: dict[str, int], pricing: dict[str, float | str]) -> dict[str, float]:
    a = cost(total["input"], float(pricing["input_per_1m"]))
    b = cost(total["output"], float(pricing["output_per_1m"]))
    c = cost(total["cache_read"], float(pricing["cache_read_per_1m"]))
    return {"input": a, "output": b, "cache": c, "billable": a + b, "total": a + b + c}


def fmt_num(value: int) -> str:
    return f"{value:,}"


def fmt_cny(value: float) -> str:
    return f"{value:.4f}"


def pct_change(old: float, new: float) -> str:
    return "N/A" if old == 0 else f"{((new - old) / old * 100):+.1f}%"
