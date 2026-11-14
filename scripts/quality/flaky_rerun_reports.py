"""Read pytest rerun telemetry; callers decide whether missing reports are fatal."""

from __future__ import annotations

import json
from pathlib import Path


def load_rerun_nodeids(paths: list[Path]) -> tuple[set[str], list[Path]]:
    nodeids: set[str] = set()
    missing: list[Path] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            tests = payload["tests"]
            if not isinstance(tests, list):
                raise AttributeError("rerun report tests must be a list")
        except (OSError, json.JSONDecodeError, AttributeError, KeyError):
            missing.append(path)
            continue
        nodeids.update(str(test) for test in tests)
    return nodeids, missing
