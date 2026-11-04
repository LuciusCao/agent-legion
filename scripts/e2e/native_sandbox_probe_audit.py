"""Fail-closed kernel-journal acquisition and per-case audit evidence."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

RELEVANT_AUDIT = ("audit", "apparmor", "seccomp", "denied", "bwrap", "unshare")


def _sanitize(text: str) -> str:
    return text.replace("native-sandbox-probe-parent-canary", "[redacted-probe-canary]").replace(
        "native-sandbox-probe-canary", "[redacted-probe-canary]"
    )


def _journal(*args: str) -> str:
    completed = subprocess.run(
        ["journalctl", "--no-pager", *args], capture_output=True, text=True, timeout=30, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"journalctl failed ({completed.returncode}): {_sanitize(completed.stderr)}"
        )
    if not completed.stdout.strip() or "-- No entries --" in completed.stdout:
        raise RuntimeError("journalctl returned no journal entries")
    if completed.stderr.strip():
        raise RuntimeError(f"journalctl diagnostics: {_sanitize(completed.stderr)}")
    return _sanitize(completed.stdout)


def _case_audit(case: dict[str, Any]) -> dict[str, Any]:
    start = math.floor((float(case["started_epoch"]) - 0.5) * 1_000_000) / 1_000_000
    end = math.ceil((float(case["ended_epoch"]) + 2.0) * 1_000_000) / 1_000_000
    try:
        raw = _journal("-k", "--since", f"@{start:.6f}", "--until", f"@{end:.6f}")
    except (RuntimeError, ValueError) as exc:
        return {
            "passed": False,
            "journal_returncode": None,
            "raw_nonempty": False,
            "relevant_count": 0,
            "error": str(exc),
            "rendered": f"<journal acquisition failed: {exc}>\n",
        }
    relevant = [
        line for line in raw.splitlines() if any(word in line.lower() for word in RELEVANT_AUDIT)
    ]
    return {
        "passed": bool(relevant),
        "journal_returncode": 0,
        "raw_nonempty": True,
        "relevant_count": len(relevant),
        "error": None,
        "rendered": "\n".join(relevant)
        + ("\n" if relevant else "<no matching kernel audit diagnostics>\n"),
    }


def collect_apparmor_status(output: Path) -> None:
    completed = subprocess.run(
        ["aa-status", "--json"], capture_output=True, text=True, timeout=30, check=False
    )
    (output / "apparmor-status.json").write_text(
        _sanitize(completed.stdout + completed.stderr), encoding="utf-8"
    )
    (output / "apparmor-collection.json").write_text(
        json.dumps(
            {
                "returncode": completed.returncode,
                "stdout_nonempty": bool(completed.stdout.strip()),
                "stderr": _sanitize(completed.stderr),
                "passed": completed.returncode == 0 and bool(completed.stdout.strip()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
