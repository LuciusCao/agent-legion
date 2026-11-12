"""Cross-check E6 payload booleans against independent parent and raw process evidence."""

from __future__ import annotations

from typing import Any

from scripts.e2e.native_sandbox_probe_e6_payload import (
    E6_PAYLOAD_CHECKS,
    _host_mounts_hidden,
)

NAMESPACE_KEYS = ("user", "mnt", "pid", "net")
CAPABILITY_KEYS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
TOP_LEVEL_FIELDS = (
    "identity",
    "status",
    "apparmor",
    "host_network",
    "mountinfo",
    "uid_map",
    "gid_map",
    "reaper",
    "descendant",
    "namespaces",
    "interfaces",
    "loopback",
    "parent_proc_visible",
    "parent_secret_present",
    "cwd_writable_observed",
    "canary",
    "stdio",
    "mount_escape",
    "handshake",
    "checks",
)


def _integer(value: Any) -> bool:
    return type(value) is int


def validate_e6_raw(expectations: Any, payload: Any) -> list[str]:
    if not isinstance(expectations, dict) or not isinstance(payload, dict):
        return ["raw_context"]
    failures = []
    required_expectations = (
        "parent_namespaces",
        "parent_pid",
        "expected_uid",
        "expected_gid",
        "parent_apparmor",
        "parent_seccomp",
        "canary_path",
        "state_dir",
        "work_path",
    )
    for key in required_expectations:
        if key not in expectations:
            failures.append(f"expectation_{key}")
    for key in TOP_LEVEL_FIELDS:
        if key not in payload:
            failures.append(f"raw_{key}")
    if failures:
        return failures

    identity = payload["identity"]
    status = payload["status"]
    parent_namespaces = expectations["parent_namespaces"]
    if not isinstance(identity, dict) or not isinstance(status, dict):
        return ["raw_identity_status"]
    if not isinstance(parent_namespaces, dict):
        return ["expectation_parent_namespaces"]
    for key in ("expected_uid", "expected_gid", "parent_pid"):
        if not _integer(expectations[key]) or expectations[key] <= 0:
            failures.append(f"expectation_{key}")
    for key in ("uid", "euid", "gid", "egid", "pid", "ppid"):
        if not _integer(identity.get(key)):
            failures.append(f"identity_{key}")
    for key in (*CAPABILITY_KEYS, "NoNewPrivs", "Seccomp"):
        if not isinstance(status.get(key), str) or not status[key]:
            failures.append(f"status_{key}")
    if failures:
        return failures

    computed: dict[str, bool] = {}
    computed["original_uid"] = (
        identity["uid"] == expectations["expected_uid"]
        and identity["euid"] == expectations["expected_uid"]
        and expectations["expected_uid"] != 0
    )
    computed["original_gid"] = (
        identity["gid"] == expectations["expected_gid"]
        and identity["egid"] == expectations["expected_gid"]
    )
    computed["no_new_privileges"] = status["NoNewPrivs"] == "1"
    computed["seccomp_unchanged"] = status["Seccomp"] == expectations["parent_seccomp"]
    try:
        computed["empty_capabilities"] = all(int(status[key], 16) == 0 for key in CAPABILITY_KEYS)
    except ValueError:
        failures.append("status_capabilities")
        computed["empty_capabilities"] = False

    namespaces = payload["namespaces"]
    if not isinstance(namespaces, dict):
        failures.append("raw_namespaces")
    else:
        for key in NAMESPACE_KEYS:
            value = namespaces.get(key)
            if not isinstance(value, str) or not value.strip():
                failures.append(f"namespace_{key}")
            elif not isinstance(parent_namespaces.get(key), str):
                failures.append(f"expectation_namespace_{key}")
            else:
                namespace_kind = {"user": "user", "mnt": "mount", "pid": "pid", "net": "network"}[
                    key
                ]
                computed[f"private_{namespace_kind}_ns"] = value != parent_namespaces[key]
    computed["private_proc"] = payload["parent_proc_visible"] is False
    if payload["interfaces"] != ["lo"]:
        computed["only_loopback"] = False
    else:
        computed["only_loopback"] = True
    loopback = payload["loopback"]
    if not isinstance(loopback, dict) or not _integer(loopback.get("flags")):
        failures.append("raw_loopback")
    else:
        computed["loopback_up"] = bool(loopback["flags"] & 0x1)
        computed["loopback_address"] = loopback.get("ipv4") == "127.0.0.1"

    host_network = payload["host_network"]
    if not isinstance(host_network, dict):
        failures.append("raw_host_network")
    else:
        for name in ("loopback", "nonloopback", "abstract_unix"):
            row = host_network.get(name)
            if (
                not isinstance(row, dict)
                or type(row.get("connected")) is not bool
                or not isinstance(row.get("detail"), str)
                or not row["detail"]
            ):
                failures.append(f"host_network_{name}")
            else:
                computed[f"host_{name}_denied"] = row["connected"] is False

    computed["initial_payload_pid2"] = identity["pid"] == 2 and identity["ppid"] == 1
    computed["default_reaper"] = isinstance(payload["reaper"], str) and "bwrap" in payload["reaper"]
    descendant = payload["descendant"]
    if not isinstance(descendant, dict):
        failures.append("raw_descendant")
    else:
        computed["descendant_not_pid1"] = (
            descendant.get("returncode") == 0
            and _integer(descendant.get("pid"))
            and descendant["pid"] != 1
            and descendant.get("reaper_visible") is True
        )
    computed["cwd_writable"] = payload["cwd_writable_observed"] is True
    canary = payload["canary"]
    if not isinstance(canary, dict):
        failures.append("raw_canary")
    else:
        computed["canary_read_denied"] = canary.get("read_denied") is True
        computed["canary_write_denied"] = canary.get("write_denied") is True
    computed["parent_secret_env_absent"] = payload["parent_secret_present"] is False

    stdio = payload["stdio"]
    if not isinstance(stdio, dict):
        failures.append("raw_stdio")
    else:
        observed = stdio.get("observed")
        enumeration = stdio.get("enumeration_fds")
        if (
            not isinstance(observed, list)
            or not all(_integer(fd) for fd in observed)
            or not isinstance(enumeration, list)
            or len(enumeration) != 1
            or not all(_integer(fd) for fd in enumeration)
        ):
            failures.append("raw_stdio_fds")
        else:
            computed["stdio_only"] = set(observed) == {0, 1, 2, enumeration[0]}

    computed["parent_unconfined"] = expectations["parent_apparmor"] == "unconfined"
    apparmor = payload["apparmor"]
    computed["payload_unpriv_stack"] = (
        isinstance(apparmor, dict) and apparmor.get("current") == "bwrap//&unpriv_bwrap (enforce)"
    )
    computed["uid_map_original"] = isinstance(payload["uid_map"], str) and payload[
        "uid_map"
    ].split()[:3] == [str(expectations["expected_uid"]), str(expectations["expected_uid"]), "1"]
    computed["gid_map_original"] = isinstance(payload["gid_map"], str) and payload[
        "gid_map"
    ].split()[:3] == [str(expectations["expected_gid"]), str(expectations["expected_gid"]), "1"]
    mount_escape = payload["mount_escape"]
    if not isinstance(mount_escape, dict) or not isinstance(mount_escape.get("attempts"), list):
        failures.append("raw_mount_escape")
    elif len(mount_escape["attempts"]) != 3:
        failures.append("raw_mount_escape_attempts")
    else:
        returncodes = [
            row.get("returncode") for row in mount_escape["attempts"] if isinstance(row, dict)
        ]
        computed["mount_escape_denied"] = (
            len(returncodes) == 3
            and all(_integer(code) and code != 0 for code in returncodes)
            and mount_escape.get("denied") is True
        )
    if not isinstance(payload["mountinfo"], str) or not payload["mountinfo"]:
        failures.append("raw_mountinfo")
    else:
        computed["host_mounts_hidden"] = _host_mounts_hidden(expectations, payload["mountinfo"])
    handshake = payload["handshake"]
    computed["launcher_handshake"] = (
        isinstance(handshake, dict) and handshake.get("release_observed") is True
    )

    checks = payload["checks"]
    if not isinstance(checks, dict):
        failures.append("raw_checks")
    else:
        for name in E6_PAYLOAD_CHECKS:
            if name not in computed:
                failures.append(f"raw_rule_{name}")
            elif checks.get(name) is not computed[name]:
                failures.append(f"raw_check_{name}")
    return failures


def validate_listener_evidence(execution: dict[str, Any]) -> list[str]:
    listeners = execution.get("candidate_listeners")
    if not isinstance(listeners, dict):
        return ["candidate_listeners"]
    failures = []
    for name in ("loopback", "nonloopback", "abstract_unix"):
        row = listeners.get(name)
        if not isinstance(row, dict):
            failures.append(f"candidate_listener_{name}")
            continue
        for control_name in ("before_control", "after_control"):
            control = row.get(control_name)
            if not isinstance(control, dict):
                failures.append(f"{name}_{control_name}")
                continue
            before = control.get("before")
            after = control.get("after")
            if (
                control.get("connected") is not True
                or control.get("token_valid") is not True
                or not _integer(before)
                or not _integer(after)
                or after - before != 1
            ):
                failures.append(f"{name}_{control_name}")
        initial = row.get("before")
        candidate_before = row.get("candidate_before")
        candidate_after = row.get("candidate_after")
        final = row.get("after")
        if not all(
            _integer(value) for value in (initial, candidate_before, candidate_after, final)
        ):
            failures.append(f"candidate_listener_{name}_counts")
            continue
        if candidate_before - initial != 1 or candidate_after != candidate_before:
            failures.append(f"candidate_listener_{name}_candidate_delta")
        if final - candidate_after != 1:
            failures.append(f"candidate_listener_{name}_final_delta")
        before_control = row.get("before_control")
        after_control = row.get("after_control")
        if isinstance(before_control, dict) and (
            before_control.get("before") != initial
            or before_control.get("after") != candidate_before
        ):
            failures.append(f"{name}_before_control_counts")
        if isinstance(after_control, dict) and (
            after_control.get("before") != candidate_after or after_control.get("after") != final
        ):
            failures.append(f"{name}_after_control_counts")
    return failures


def validate_cleanup_namespace_paths(row: dict[str, Any]) -> list[str]:
    pids = row.get("observed_pids")
    paths = row.get("namespace_paths")
    if (
        not isinstance(pids, list)
        or len(pids) < 2
        or not all(_integer(pid) and pid > 0 for pid in pids)
    ):
        return ["observed_pids"]
    if len(set(pids)) != len(pids):
        return ["observed_pids_duplicate"]
    expected = [f"/proc/{pid}/ns/{name}" for pid in pids for name in NAMESPACE_KEYS]
    if not isinstance(paths, list) or paths != expected:
        return ["namespace_paths"]
    return []
