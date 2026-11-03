"""Payload evidence for the candidate-only direct-bwrap AppArmor experiment."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e.native_sandbox_probe_payload import (  # noqa: E402
    LISTENER_TOKEN,
    _base_document,
    _read,
    _run_e5,
)

E6_PAYLOAD_CHECKS = (
    "original_uid",
    "original_gid",
    "no_new_privileges",
    "seccomp_unchanged",
    "empty_capabilities",
    "private_user_ns",
    "private_mount_ns",
    "private_pid_ns",
    "private_proc",
    "private_network_ns",
    "only_loopback",
    "loopback_up",
    "loopback_address",
    "host_loopback_denied",
    "host_nonloopback_denied",
    "host_abstract_unix_denied",
    "initial_payload_pid2",
    "default_reaper",
    "descendant_not_pid1",
    "cwd_writable",
    "canary_read_denied",
    "canary_write_denied",
    "parent_secret_env_absent",
    "stdio_only",
    "parent_unconfined",
    "payload_unpriv_stack",
    "uid_map_original",
    "gid_map_original",
    "mount_escape_denied",
    "host_mounts_hidden",
    "launcher_handshake",
)


def check_e6_payload_failures(document: dict[str, Any]) -> list[str]:
    checks = document.get("checks")
    if not isinstance(checks, dict):
        return list(E6_PAYLOAD_CHECKS)
    return [name for name in E6_PAYLOAD_CHECKS if checks.get(name) is not True]


def _tcp_attempt(host: str, port: int) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=2) as connection:
            received = connection.recv(len(LISTENER_TOKEN))
    except OSError as exc:
        return {"connected": False, "detail": f"{type(exc).__name__}: {exc.errno}"}
    return {"connected": received == LISTENER_TOKEN, "detail": "connected"}


def _abstract_unix_attempt(name: str) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect("\0" + name)
            received = connection.recv(len(LISTENER_TOKEN))
    except OSError as exc:
        return {"connected": False, "detail": f"{type(exc).__name__}: {exc.errno}"}
    return {"connected": received == LISTENER_TOKEN, "detail": "connected"}


def _mount_escape() -> dict[str, Any]:
    target = Path.cwd() / "escape-target"
    target.mkdir(exist_ok=True)
    attempts = [
        ["/bin/mount", "-t", "tmpfs", "tmpfs", str(target)],
        ["/bin/umount", str(target)],
        ["/usr/sbin/pivot_root", ".", "."],
    ]
    results = []
    for command in attempts:
        completed = subprocess.run(command, capture_output=True, timeout=5, check=False)
        results.append({"command": command, "returncode": completed.returncode})
    return {"attempts": results, "denied": all(row["returncode"] != 0 for row in results)}


def _host_mounts_hidden(config: dict[str, Any], mountinfo: str) -> bool:
    return config["canary_path"] not in mountinfo and all(
        field == config["work_path"] or config["state_dir"] not in field
        for line in mountinfo.splitlines()
        for field in line.split()
    )


def _handshake(config: dict[str, Any]) -> bool:
    ready = Path(config["ready_path"])
    ready.write_text(json.dumps({"pid": os.getpid()}) + "\n", encoding="utf-8")
    release = Path(config["release_path"])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if release.is_file():
            return True
        time.sleep(0.01)
    return False


def run(config: dict[str, Any]) -> dict[str, Any]:
    document = _base_document(config)
    _run_e5(config, document)
    checks = document["checks"]
    checks.pop("loopback_down")
    checks.pop("network_denied")
    document["apparmor"] = {"current": _read(Path("/proc/self/attr/current"))}
    document["uid_map"] = _read(Path("/proc/self/uid_map"))
    document["gid_map"] = _read(Path("/proc/self/gid_map"))
    document["host_network"] = {
        "loopback": document["network"],
        "nonloopback": _tcp_attempt(config["nonloopback_host"], int(config["nonloopback_port"])),
        "abstract_unix": _abstract_unix_attempt(config["abstract_socket"]),
    }
    document["mount_escape"] = _mount_escape()
    document["mountinfo"] = _read(Path("/proc/self/mountinfo"))
    handshake = _handshake(config)
    checks.update(
        {
            "loopback_up": bool(document["loopback"]["flags"] & 0x1),
            "loopback_address": document["loopback"]["ipv4"] == "127.0.0.1",
            "host_loopback_denied": document["host_network"]["loopback"]["connected"] is False,
            "host_nonloopback_denied": document["host_network"]["nonloopback"]["connected"]
            is False,
            "host_abstract_unix_denied": document["host_network"]["abstract_unix"]["connected"]
            is False,
            "parent_unconfined": config["parent_apparmor"] == "unconfined",
            "seccomp_unchanged": document["status"]["Seccomp"] == config["parent_seccomp"],
            "payload_unpriv_stack": document["apparmor"]["current"]
            == "bwrap//&unpriv_bwrap (enforce)",
            "uid_map_original": document["uid_map"].split()[:3]
            == [str(config["expected_uid"]), str(config["expected_uid"]), "1"],
            "gid_map_original": document["gid_map"].split()[:3]
            == [str(config["expected_gid"]), str(config["expected_gid"]), "1"],
            "mount_escape_denied": document["mount_escape"]["denied"],
            "host_mounts_hidden": _host_mounts_hidden(config, document["mountinfo"]),
            "launcher_handshake": handshake,
        }
    )
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    document = run(config)
    Path(config["output"]).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    failures = check_e6_payload_failures(document)
    if failures:
        print(f"E6 payload invariant failures: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
