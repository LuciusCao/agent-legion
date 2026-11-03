"""Fail-closed raw-evidence contract for the direct-bwrap E6 candidate."""

from __future__ import annotations

from typing import Any

from scripts.e2e.native_sandbox_probe_contract import (
    NAMESPACE_KEYS,
    _baseline_failures,
    _execution_failures,
)
from scripts.e2e.native_sandbox_probe_e6_evidence import (
    validate_cleanup_namespace_paths,
    validate_e6_raw,
    validate_listener_evidence,
)
from scripts.e2e.native_sandbox_probe_e6_payload import check_e6_payload_failures

E6_CASES = ("e6-n0-single", "e6-n0-concurrent-a", "e6-n0-concurrent-b")
CLEANUP_MODES = ("signal", "timeout", "parent-death")
LISTENERS = ("loopback", "nonloopback", "abstract_unix")


def _main_failures(execution: dict[str, Any]) -> list[str]:
    main = execution.get("bwrap_main")
    if not isinstance(main, dict):
        return ["bwrap_main"]
    failures = []
    if main.get("apparmor_current") != "bwrap (enforce)":
        failures.append("bwrap_main_profile")
    if main.get("pid") != execution.get("launcher_pid"):
        failures.append("bwrap_main_pid")
    status = main.get("status")
    if not isinstance(status, dict) or status.get("NoNewPrivs") != "1":
        failures.append("bwrap_main_nnp")
    return failures


def _listener_failures(execution: dict[str, Any]) -> list[str]:
    return validate_listener_evidence(execution)


def _cleanup_failures(execution: dict[str, Any]) -> list[str]:
    trials = execution.get("cleanup_trials")
    if not isinstance(trials, list) or len(trials) != len(CLEANUP_MODES):
        return ["cleanup_trials"]
    failures = []
    by_mode = {row.get("mode"): row for row in trials if isinstance(row, dict)}
    for mode in CLEANUP_MODES:
        row = by_mode.get(mode)
        if not isinstance(row, dict):
            failures.append(f"{mode}_cleanup")
            continue
        for field in (
            "expected_signal_used",
            "process_group_cleaned",
            "processes_reaped",
            "namespaces_released",
        ):
            if row.get(field) is not True:
                failures.append(f"{mode}_{field}")
        if type(row.get("launcher_pid")) is not int or row["launcher_pid"] <= 0:
            failures.append(f"{mode}_launcher_pid")
        failures.extend(f"{mode}_{failure}" for failure in validate_cleanup_namespace_paths(row))
    return failures


def validate_e6_result(case: str, result: Any, *, require_cleanup: bool = True) -> list[str]:
    if not isinstance(result, dict):
        return ["result"]
    failures = _baseline_failures(result)
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return [*failures, "execution"]
    failures.extend(_execution_failures(case, execution))
    failures.extend(_main_failures(execution))
    failures.extend(_listener_failures(execution))
    if execution.get("payload_started") is not True:
        failures.append("payload_started")
    if case == "e6-n0-single" and require_cleanup:
        failures.extend(_cleanup_failures(execution))
    if execution.get("returncode") == 0:
        payload = result.get("payload")
        if not isinstance(payload, dict):
            failures.append("payload")
        else:
            if payload.get("case") != case:
                failures.append("payload_case")
            failures.extend(check_e6_payload_failures(payload))
            failures.extend(validate_e6_raw(result.get("expectations"), payload))
            namespaces = payload.get("namespaces")
            if not isinstance(namespaces, dict):
                failures.append("namespaces")
            else:
                failures.extend(
                    f"namespace_{name}"
                    for name in NAMESPACE_KEYS
                    if not isinstance(namespaces.get(name), str) or not namespaces[name].strip()
                )
    return failures


def e6_result_passed(case: str, result: Any) -> bool:
    return not validate_e6_result(case, result)


def validate_e6_independence(results: dict[str, Any]) -> list[str]:
    failures = []
    for namespace in NAMESPACE_KEYS:
        observed = []
        for case in E6_CASES:
            result = results.get(case)
            payload = result.get("payload") if isinstance(result, dict) else None
            namespaces = payload.get("namespaces") if isinstance(payload, dict) else None
            value = namespaces.get(namespace) if isinstance(namespaces, dict) else None
            if not isinstance(value, str) or not value.strip():
                failures.append(f"{case}:namespace_{namespace}")
            else:
                observed.append(value)
        if len(observed) == len(E6_CASES) and len(set(observed)) != len(observed):
            failures.append(f"duplicate_namespace_{namespace}")
    return failures


def validate_rollback(results: dict[str, Any]) -> list[str]:
    failures = []
    for case in E6_CASES:
        result = results.get(case)
        if not isinstance(result, dict) or result.get("evidence_collected") is not True:
            failures.append(f"{case}:missing")
            continue
        execution = result.get("execution")
        if not isinstance(execution, dict):
            failures.append(f"{case}:execution")
            continue
        if type(execution.get("returncode")) is not int or execution["returncode"] == 0:
            failures.append(f"{case}:unexpected_success")
        if execution.get("payload_started") is not False or "payload" in result:
            failures.append(f"{case}:not_pre_payload")
        if execution.get("process_group_cleaned") is not True:
            failures.append(f"{case}:cleanup")
    return failures
