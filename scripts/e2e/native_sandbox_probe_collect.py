"""Collect read-only host audit evidence for the disposable native probe."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e.native_sandbox_probe_audit import (  # noqa: E402
    _case_audit,
    _journal,
    collect_apparmor_status,
)
from scripts.e2e.native_sandbox_probe_audit import _sanitize as _sanitize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    if args.evidence.is_dir():
        shutil.copytree(args.evidence, args.output, dirs_exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "systemd-journal.log").write_text(
        _journal(
            "-u",
            args.unit,
            "-u",
            "agent-legion-native-sandbox-probe-e6@targeted.service",
            "-u",
            "agent-legion-native-sandbox-probe-e6@rollback.service",
        ),
        encoding="utf-8",
    )
    collect_apparmor_status(args.output)
    cases = []
    for summary_name in ("summary.json", "candidate-summary.json"):
        summary_path = args.output / summary_name
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            cases.extend(summary.get("cases", []))
    if not cases:
        cases = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(args.output.glob("cases/*/result.json"))
        ]
    for case in cases:
        case_dir = args.output / "cases" / case["case"]
        case_dir.mkdir(parents=True, exist_ok=True)
        audit = _case_audit(case)
        (case_dir / "kernel-audit.log").write_text(audit.pop("rendered"), encoding="utf-8")
        (case_dir / "kernel-audit.json").write_text(
            json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
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
