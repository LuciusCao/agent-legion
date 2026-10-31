"""Shared real payload for the native Ubuntu sandbox spike (test/CI only)."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any

CLAIM_SECRET = "native-spike-claim-secret"
PARENT_SECRET_ENV = "NATIVE_SPIKE_PARENT_SECRET"
LISTENER_TOKEN = b"native-spike-listener-v1\n"
REQUIRED_CHECKS = (
    "nonroot_uid",
    "no_new_privileges",
    "empty_capabilities",
    "private_user_ns",
    "private_mount_ns",
    "private_pid_ns",
    "private_proc",
    "pid_one_when_expected",
    "expected_network_ns",
    "network_policy",
    "cwd_writable",
    "canary_read_denied",
    "canary_write_denied",
    "parent_secret_env_absent",
    "stdio_only",
    "claim_secret_channel",
)


def check_failures(document: dict[str, Any]) -> list[str]:
    checks = document.get("checks")
    if not isinstance(checks, dict):
        return list(REQUIRED_CHECKS)
    return [name for name in REQUIRED_CHECKS if checks.get(name) is not True]


def _namespace(name: str) -> str:
    return os.readlink(Path("/proc/self/ns") / name)


def _status() -> dict[str, str]:
    rows = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            rows[key] = value.strip()
    return rows


def _canary_denied(path: Path, write: bool) -> bool:
    try:
        if write:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("sandbox-escape\n")
        else:
            with path.open("rb") as handle:
                handle.read(1)
    except OSError:
        return True
    return False


def _network_probe(config: dict[str, Any]) -> tuple[bool, str]:
    allow = config["allow_network"] is True
    try:
        with socket.create_connection(
            (config["listener_host"], int(config["listener_port"])), timeout=3
        ) as connection:
            received = connection.recv(len(LISTENER_TOKEN))
    except OSError as exc:
        return not allow, type(exc).__name__
    return allow and received == LISTENER_TOKEN, "connected"


def run(job: dict[str, Any], job_dir: str, runtime: dict[str, Any]) -> None:
    del job
    probe = runtime["node_config"]["probe"]
    config = json.loads(probe) if isinstance(probe, str) else probe
    root = Path(job_dir)
    parent_namespaces = config["parent_namespaces"]
    namespaces = {name: _namespace(name) for name in ("user", "mnt", "pid", "net")}
    status = _status()
    capabilities_zero = all(
        int(status.get(name, "1"), 16) == 0
        for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    )
    network_ok, network_detail = _network_probe(config)
    write_probe = root / ".native-spike-write-probe"
    try:
        write_probe.write_text("ok", encoding="utf-8")
        write_probe.unlink()
        cwd_writable = True
    except OSError:
        cwd_writable = False
    allow_network = config["allow_network"] is True
    expected_pid_one = config.get("expect_pid_one") is True
    parent_pid = int(config["parent_pid"])
    checks = {
        "nonroot_uid": os.getuid() != 0 and os.geteuid() != 0,
        "no_new_privileges": status.get("NoNewPrivs") == "1",
        "empty_capabilities": capabilities_zero,
        "private_user_ns": namespaces["user"] != parent_namespaces["user"],
        "private_mount_ns": namespaces["mnt"] != parent_namespaces["mnt"],
        "private_pid_ns": namespaces["pid"] != parent_namespaces["pid"],
        "private_proc": not Path(f"/proc/{parent_pid}").exists() and Path("/proc/1").exists(),
        "pid_one_when_expected": not expected_pid_one or os.getpid() == 1,
        "expected_network_ns": (namespaces["net"] == parent_namespaces["net"]) is allow_network,
        "network_policy": network_ok,
        "cwd_writable": cwd_writable,
        "canary_read_denied": _canary_denied(Path(config["canary_path"]), write=False),
        "canary_write_denied": _canary_denied(Path(config["canary_path"]), write=True),
        "parent_secret_env_absent": PARENT_SECRET_ENV not in os.environ,
        "stdio_only": {int(name) for name in os.listdir("/proc/self/fd")} == {0, 1, 2},
        "claim_secret_channel": runtime["node_config"].get("claim_secret") == CLAIM_SECRET,
    }
    document = {
        "schema_version": 1,
        "case": config["case"],
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "pid": os.getpid(),
        "namespaces": namespaces,
        "network": {"allowed": allow_network, "detail": network_detail},
        "checks": checks,
    }
    (root / config["output_name"]).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    failures = check_failures(document)
    if failures:
        raise RuntimeError(f"native sandbox probe failed: {', '.join(failures)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    run(
        {},
        str(args.config.parent),
        {"node_config": {"probe": config, "claim_secret": CLAIM_SECRET}},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
