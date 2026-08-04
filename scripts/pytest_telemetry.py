"""Small pytest plugin that records rerun attempts for CI telemetry."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_RERUNS: list[dict[str, str]] = []


def pytest_sessionstart(session: Any) -> None:
    """Reset controller-local state before a test session starts."""
    _RERUNS.clear()


def pytest_runtest_logreport(report: Any) -> None:
    """Collect reports emitted by pytest-rerunfailures."""
    if getattr(report, "outcome", None) != "rerun":
        return
    _RERUNS.append(
        {
            "nodeid": str(getattr(report, "nodeid", "unknown")),
            "phase": str(getattr(report, "when", "unknown")),
        }
    )


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
    tests = sorted({entry["nodeid"] for entry in _RERUNS})
    payload = {
        "attempts": len(_RERUNS),
        "exitstatus": int(exitstatus),
        "tests": tests,
        "reports": _RERUNS,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
