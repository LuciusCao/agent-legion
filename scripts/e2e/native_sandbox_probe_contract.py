"""Authoritative raw-evidence contracts for the native sandbox probe."""

from __future__ import annotations

from typing import Any

from scripts.e2e.native_sandbox_probe_payload import E5_CHECKS, check_failures

NAMESPACE_KEYS = ("user", "mnt", "pid", "net")
E5_CASES = ("e5-n0-single", "e5-n0-concurrent-a", "e5-n0-concurrent-b")
E5_PREREQUISITES = ("e0-n0-bwrap-no-net-control", "e1-n0-user-net-namespace")
CURRENT_CHECKS = (
    "nonroot_uid",
    "no_new_privileges",
    "empty_capabilities",
    "private_user_ns",
    "private_mount_ns",
    "private_pid_ns",
    "private_proc",
    "initial_payload_pid2",
    "default_reaper",
    "expected_network_ns",
    "network_policy",
    "stdio_only",
)
E1_NET_CHECKS = (
    "outer_root_mapping",
    "private_user_ns",
    "private_network_ns",
    "only_loopback",
    "loopback_down",
)
E2_LOOPBACK_CHECKS = ("only_loopback", "loopback_up", "loopback_address")
PAYLOAD_FREE_CASE = "e1-n0-user-namespace"


def required_checks(case: str) -> tuple[str, ...]:
    if case == PAYLOAD_FREE_CASE:
        return ()
    if case.startswith("e5-"):
        return E5_CHECKS
    if case == "e1-n0-user-net-namespace":
        return E1_NET_CHECKS
    if case == "e2-n0-private-loopback":
        return E2_LOOPBACK_CHECKS
    return CURRENT_CHECKS


def _baseline_failures(result: dict[str, Any]) -> list[str]:
    baseline = result.get("baseline")
    if not isinstance(baseline, dict):
        return ["baseline"]
    sha = baseline.get("git_sha")
    if (
        not isinstance(sha, str)
        or len(sha) != 40
        or any(character not in "0123456789abcdef" for character in sha)
    ):
        return ["baseline_git_sha"]
    return []


def _execution_failures(case: str, execution: Any) -> list[str]:
    if not isinstance(execution, dict):
        return ["execution"]
    failures = []
    returncode = execution.get("returncode")
    if type(returncode) is not int or returncode != 0:
        failures.append("returncode")
    if execution.get("timed_out") is not False:
        failures.append("timed_out")
    if execution.get("process_group_cleaned") is not True:
        failures.append("process_group_cleaned")
    before = execution.get("listener_connections_before")
    after = execution.get("listener_connections_after")
    expected = 1 if case.endswith("bwrap-no-net-control") else 0
    if type(before) is not int or type(after) is not int or after - before != expected:
        failures.append("listener_connections")
    if type(execution.get("launcher_pid")) is not int or execution["launcher_pid"] <= 0:
        failures.append("launcher_pid")
    if not isinstance(execution.get("command_log"), str) or not execution["command_log"]:
        failures.append("command_log")
    if not isinstance(execution.get("stderr_tail"), str):
        failures.append("stderr_tail")
    return failures


def _payload_failures(case: str, payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["payload"]
    failures = []
    if payload.get("case") != case:
        failures.append("payload_case")
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        failures.append("checks")
    else:
        if case.startswith("e5-"):
            failures.extend(check_failures(payload))
        else:
            failures.extend(name for name in required_checks(case) if checks.get(name) is not True)
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


def validate_result(case: str, result: Any) -> list[str]:
    if not isinstance(result, dict):
        return ["result"]
    failures = _baseline_failures(result)
    execution = result.get("execution")
    failures.extend(_execution_failures(case, execution))
    if (
        isinstance(execution, dict)
        and execution.get("returncode") == 0
        and case != PAYLOAD_FREE_CASE
    ):
        failures.extend(_payload_failures(case, result.get("payload")))
    return failures


def result_passed(case: str, result: Any) -> bool:
    return not validate_result(case, result)


def validate_e5_independence(results: dict[str, Any]) -> list[str]:
    failures = []
    for namespace in NAMESPACE_KEYS:
        observed = []
        for case in E5_CASES:
            result = results.get(case)
            payload = result.get("payload") if isinstance(result, dict) else None
            namespaces = payload.get("namespaces") if isinstance(payload, dict) else None
            value = namespaces.get(namespace) if isinstance(namespaces, dict) else None
            if not isinstance(value, str) or not value.strip():
                failures.append(f"{case}:namespace_{namespace}")
            else:
                observed.append(value)
        if len(observed) == len(E5_CASES) and len(set(observed)) != len(observed):
            failures.append(f"duplicate_namespace_{namespace}")
    return failures
