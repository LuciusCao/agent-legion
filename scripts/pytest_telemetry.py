"""Small pytest plugin that records rerun attempts for CI telemetry."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_FAILURE_TEXT_LIMIT = 32_000
_SECTION_TEXT_LIMIT = 8_000
_TRUNCATION_MARKER = "\n... <truncated by pytest telemetry>"
_DSN_RE = re.compile(r"\bpostgres(?:ql)?://[^\s'\"<>]+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|client[_-]?secret|dsn|password|passwd|secret|token)\s*[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+"
)
_TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._~+/=-]+|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)
_CAPTURED_SECTIONS = ("Captured stdout", "Captured stderr", "Captured log")

_RERUNS: list[dict[str, Any]] = []


def _diagnostic_text(value: Any, limit: int) -> dict[str, Any]:
    text = str(value)
    text = _DSN_RE.sub("<redacted-dsn>", text)
    text = _SECRET_RE.sub(r"\1<redacted-secret>", text)
    text = _TOKEN_RE.sub("<redacted-token>", text)
    truncated = len(text) > limit
    text = text[:limit] + _TRUNCATION_MARKER if truncated else text
    return {"text": text, "truncated": truncated}


def pytest_sessionstart(session: Any) -> None:
    """Reset controller-local state before a test session starts."""
    _RERUNS.clear()


def pytest_runtest_logreport(report: Any) -> None:
    """Collect bounded diagnostics only for failures hidden by a retry-pass."""
    if getattr(report, "outcome", None) != "rerun":
        return
    entry: dict[str, Any] = {"nodeid": str(report.nodeid), "phase": str(report.when)}
    longrepr = getattr(report, "longrepr", None)
    if longrepr is not None:
        entry["failure"] = _diagnostic_text(longrepr, _FAILURE_TEXT_LIMIT)
    sections = [
        {"name": str(name), **_diagnostic_text(value, _SECTION_TEXT_LIMIT)}
        for name, value in getattr(report, "sections", ())
        if str(name).startswith(_CAPTURED_SECTIONS)
    ]
    if sections:
        entry["sections"] = sections
    _RERUNS.append(entry)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Write one report from the xdist controller, never from workers."""
    config = session.config
    if hasattr(config, "workerinput"):
        return
    output = os.environ.get("AGENT_LEGION_RERUN_REPORT")
    if not output:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "attempts": len(_RERUNS),
        "exitstatus": int(exitstatus),
        "tests": sorted({entry["nodeid"] for entry in _RERUNS}),
        "reports": _RERUNS,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
