"""Collect read-only host audit evidence for the disposable native probe."""

from __future__ import annotations

import argparse
import json
import math
import shutil
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
    return _sanitize(completed.stdout + completed.stderr)


def _case_audit(case: dict[str, Any]) -> str:
    start = math.floor((float(case["started_epoch"]) - 0.5) * 1_000_000) / 1_000_000
    end = math.ceil((float(case["ended_epoch"]) + 2.0) * 1_000_000) / 1_000_000
    raw = _journal("-k", "--since", f"@{start:.6f}", "--until", f"@{end:.6f}")
    relevant = [
        line for line in raw.splitlines() if any(word in line.lower() for word in RELEVANT_AUDIT)
    ]
    return "\n".join(relevant) + ("\n" if relevant else "<no matching kernel audit diagnostics>\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    if args.evidence.is_dir():
        shutil.copytree(args.evidence, args.output, dirs_exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "systemd-journal.log").write_text(_journal("-u", args.unit), encoding="utf-8")
    aa_status = subprocess.run(
        ["aa-status", "--json"], capture_output=True, text=True, timeout=30, check=False
    )
    (args.output / "apparmor-status.json").write_text(
        _sanitize(aa_status.stdout + aa_status.stderr), encoding="utf-8"
    )
    summary_path = args.output / "summary.json"
    cases = []
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        cases = summary.get("cases", [])
    else:
        cases = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(args.output.glob("cases/*/result.json"))
        ]
    for case in cases:
        case_dir = args.output / "cases" / case["case"]
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "kernel-audit.log").write_text(_case_audit(case), encoding="utf-8")
    if cases:
        start = min(float(case["started_epoch"]) for case in cases) - 1
        end = max(float(case["ended_epoch"]) for case in cases) + 3
        (args.output / "kernel-audit.log").write_text(
            _journal("-k", "--since", f"@{start:.6f}", "--until", f"@{end:.6f}"),
            encoding="utf-8",
        )
    collection = {
        "schema_version": 1,
        "case_audit_logs": [f"cases/{case['case']}/kernel-audit.log" for case in cases],
        "apparmor_policy_status": "apparmor-status.json",
        "kernel_audit": "kernel-audit.log",
        "systemd_journal": "systemd-journal.log",
        "collection_is_read_only": True,
    }
    (args.output / "collection.json").write_text(
        json.dumps(collection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
