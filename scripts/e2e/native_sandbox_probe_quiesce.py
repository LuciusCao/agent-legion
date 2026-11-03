"""Stop and prove reclamation of every old native-probe execution before rollback."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from scripts.e2e.native_sandbox_probe_apparmor import _require_root_ci
from scripts.e2e.native_sandbox_probe_apparmor_state import write_evidence_atomic

UNITS = (
    "agent-legion-native-sandbox-probe-e6@targeted.service",
    "agent-legion-native-sandbox-probe.service",
)


def _command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _execution_pids(
    stock_path: Path, candidate_path: Path
) -> tuple[list[int], list[int], list[str]]:
    pids = set()
    pgids = set()
    errors = []
    for path in (stock_path, candidate_path):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            cases = document["cases"]
            if not isinstance(cases, list):
                raise ValueError("cases must be a list")
            for case in cases:
                execution = case.get("execution")
                if not isinstance(execution, dict):
                    continue
                launcher = execution.get("launcher_pid")
                if type(launcher) is int and launcher > 0:
                    pids.add(launcher)
                    pgids.add(launcher)
                for trial in execution.get("cleanup_trials", []):
                    for pid in trial.get("observed_pids", []):
                        if type(pid) is int and pid > 0:
                            pids.add(pid)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return sorted(pids), sorted(pgids), errors


def _remaining(pids: list[int], pgids: list[int]) -> dict[str, list[int]]:
    remaining_pids = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
    remaining_pgids = []
    for pgid in pgids:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            continue
        remaining_pgids.append(pgid)
    return {"pids": remaining_pids, "pgids": remaining_pgids}


def _wait_reclaimed(pids: list[int], pgids: list[int]) -> dict[str, list[int]]:
    deadline = time.monotonic() + 10
    remaining = _remaining(pids, pgids)
    while (remaining["pids"] or remaining["pgids"]) and time.monotonic() < deadline:
        time.sleep(0.05)
        remaining = _remaining(pids, pgids)
    return remaining


def quiesce(stock_path: Path, candidate_path: Path, evidence_path: Path) -> bool:
    _require_root_ci()
    pids, pgids, errors = _execution_pids(stock_path, candidate_path)
    unit_results = []
    for unit in UNITS:
        stopped = _command(["systemctl", "stop", unit])
        active = _command(["systemctl", "is-active", unit])
        active_state = active["stdout"]
        inactive = active_state in {"inactive", "failed"} and active["returncode"] in {0, 1, 3, 4}
        unit_results.append(
            {
                "unit": unit,
                "stop": stopped,
                "is_active": active,
                "inactive": inactive,
            }
        )
    remaining = _wait_reclaimed(pids, pgids)
    quiesced = (
        not errors
        and bool(pids)
        and bool(pgids)
        and all(row["stop"]["returncode"] == 0 and row["inactive"] for row in unit_results)
        and not remaining["pids"]
        and not remaining["pgids"]
    )
    write_evidence_atomic(
        evidence_path,
        {
            "schema_version": 1,
            "operation": "quiesce",
            "quiesced": quiesced,
            "unit_results": unit_results,
            "observed_pids": pids,
            "observed_pgids": pgids,
            "remaining": remaining,
            "errors": errors,
        },
    )
    return quiesced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-summary", type=Path, required=True)
    parser.add_argument("--candidate-summary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        quiesced = quiesce(args.stock_summary, args.candidate_summary, args.evidence)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        write_evidence_atomic(
            args.evidence,
            {
                "schema_version": 1,
                "operation": "quiesce",
                "quiesced": False,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return 1
    return 0 if quiesced else 1


if __name__ == "__main__":
    raise SystemExit(main())
