"""Payload-side evidence for the disposable native sandbox network probe."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import struct
import subprocess
from pathlib import Path
from typing import Any

PARENT_SECRET_ENV = "NATIVE_SANDBOX_PROBE_PARENT_SECRET"
LISTENER_TOKEN = b"native-sandbox-probe-listener-v1\n"
E5_CHECKS = (
    "original_uid",
    "original_gid",
    "no_new_privileges",
    "empty_capabilities",
    "private_user_ns",
    "private_mount_ns",
    "private_pid_ns",
    "private_proc",
    "private_network_ns",
    "only_loopback",
    "loopback_down",
    "network_denied",
    "initial_payload_pid2",
    "default_reaper",
    "descendant_not_pid1",
    "cwd_writable",
    "canary_read_denied",
    "canary_write_denied",
    "parent_secret_env_absent",
    "stdio_only",
)


def check_failures(document: dict[str, Any]) -> list[str]:
    checks = document.get("checks")
    if not isinstance(checks, dict):
        return list(E5_CHECKS)
    return [name for name in E5_CHECKS if checks.get(name) is not True]


def _status() -> dict[str, str]:
    rows = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            rows[key] = value.strip()
    return rows


def _namespace(name: str) -> str:
    return os.readlink(Path("/proc/self/ns") / name)


def _stdio_only() -> bool:
    directory_stat = os.stat("/proc/self/fd")
    identity = (directory_stat.st_dev, directory_stat.st_ino)
    with os.scandir("/proc/self/fd") as entries:
        observed = set()
        enumeration_fds = set()
        for entry in entries:
            fd = int(entry.name)
            observed.add(fd)
            stat = entry.stat(follow_symlinks=True)
            if (stat.st_dev, stat.st_ino) == identity:
                enumeration_fds.add(fd)
    return len(enumeration_fds) == 1 and observed == {0, 1, 2} | enumeration_fds


def _interface_flags(name: str) -> int:
    request = struct.pack("16sH14s", name.encode(), 0, b"")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        response = fcntl.ioctl(sock.fileno(), 0x8913, request)
    return struct.unpack("16sH14s", response)[1]


def _interface_ipv4(name: str) -> str | None:
    request = struct.pack("16sH14s", name.encode(), socket.AF_INET, b"")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            response = fcntl.ioctl(sock.fileno(), 0x8915, request)
    except OSError:
        return None
    return socket.inet_ntoa(response[20:24])


def _network_attempt(config: dict[str, Any]) -> dict[str, Any]:
    try:
        with socket.create_connection(
            (config["listener_host"], int(config["listener_port"])), timeout=2
        ) as connection:
            received = connection.recv(len(LISTENER_TOKEN))
    except OSError as exc:
        return {"connected": False, "detail": f"{type(exc).__name__}: {exc.errno}"}
    return {"connected": received == LISTENER_TOKEN, "detail": "connected"}


def _canary_denied(path: Path, *, write: bool) -> bool:
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


def _descendant() -> dict[str, Any]:
    code = (
        "import json, os; "
        "print(json.dumps({'pid': os.getpid(), 'ppid': os.getppid(), "
        "'reaper': open('/proc/1/cmdline', 'rb').read().hex()}))"
    )
    completed = subprocess.run(
        ["/usr/bin/python3", "-c", code], capture_output=True, text=True, timeout=10, check=False
    )
    if completed.returncode != 0:
        return {"returncode": completed.returncode, "pid": None, "reaper_visible": False}
    document = json.loads(completed.stdout)
    reaper = bytes.fromhex(document.pop("reaper")).replace(b"\0", b" ").decode(errors="replace")
    return {
        "returncode": completed.returncode,
        "pid": document["pid"],
        "ppid": document["ppid"],
        "reaper_visible": "bwrap" in reaper,
    }


def _base_document(config: dict[str, Any]) -> dict[str, Any]:
    status = _status()
    interfaces = sorted(name for _, name in socket.if_nameindex())
    network = _network_attempt(config) if "listener_port" in config else None
    return {
        "schema_version": 1,
        "case": config["case"],
        "mode": config["mode"],
        "identity": {
            "uid": os.getuid(),
            "euid": os.geteuid(),
            "gid": os.getgid(),
            "egid": os.getegid(),
            "pid": os.getpid(),
            "ppid": int(status["PPid"]),
        },
        "status": {
            key: status.get(key)
            for key in (
                "NoNewPrivs",
                "Seccomp",
                "CapInh",
                "CapPrm",
                "CapEff",
                "CapBnd",
                "CapAmb",
            )
        },
        "namespaces": {name: _namespace(name) for name in ("user", "mnt", "pid", "net")},
        "interfaces": interfaces,
        "loopback": {
            "flags": _interface_flags("lo") if "lo" in interfaces else None,
            "ipv4": _interface_ipv4("lo") if "lo" in interfaces else None,
        },
        "network": network,
    }


def _reaper() -> str:
    raw = Path("/proc/1/cmdline").read_bytes()
    return raw.replace(b"\0", b" ").decode(errors="replace").strip()


def _run_e1_net(config: dict[str, Any], document: dict[str, Any]) -> None:
    parent = config["parent_namespaces"]
    document["checks"] = {
        "outer_root_mapping": os.getuid() == 0 and os.geteuid() == 0,
        "private_user_ns": document["namespaces"]["user"] != parent["user"],
        "private_network_ns": document["namespaces"]["net"] != parent["net"],
        "only_loopback": document["interfaces"] == ["lo"],
        "loopback_down": bool(document["loopback"]["flags"] & 0x8)
        and not bool(document["loopback"]["flags"] & 0x1),
    }


def _run_e2_loopback(config: dict[str, Any], document: dict[str, Any]) -> None:
    del config
    document["checks"] = {
        "only_loopback": document["interfaces"] == ["lo"],
        "loopback_up": bool(document["loopback"]["flags"] & 0x1),
        "loopback_address": document["loopback"]["ipv4"] == "127.0.0.1",
    }


def _capabilities_empty(config: dict[str, Any], document: dict[str, Any]) -> bool:
    names = ["CapInh", "CapPrm", "CapEff", "CapAmb"]
    if config["context"] == "n0" or config["context"].endswith("cap-empty"):
        names.append("CapBnd")
    return all(int(document["status"].get(name, "1"), 16) == 0 for name in names)


def _run_current(config: dict[str, Any], document: dict[str, Any], *, no_net: bool) -> None:
    parent = config["parent_namespaces"]
    capabilities_zero = _capabilities_empty(config, document)
    document["reaper"] = _reaper()
    document["checks"] = {
        "nonroot_uid": os.getuid() != 0 and os.geteuid() != 0,
        "no_new_privileges": document["status"]["NoNewPrivs"] == "1"
        if config["context"] == "n0"
        else True,
        "empty_capabilities": capabilities_zero,
        "private_user_ns": document["namespaces"]["user"] != parent["user"],
        "private_mount_ns": document["namespaces"]["mnt"] != parent["mnt"],
        "private_pid_ns": document["namespaces"]["pid"] != parent["pid"],
        "private_proc": not Path(f"/proc/{config['parent_pid']}").exists(),
        "initial_payload_pid2": os.getpid() == 2 and document["identity"]["ppid"] == 1,
        "default_reaper": "bwrap" in document["reaper"],
        "expected_network_ns": (document["namespaces"]["net"] == parent["net"]) is no_net,
        "network_policy": document["network"]["connected"] is no_net,
        "stdio_only": _stdio_only(),
    }


def _run_e5(config: dict[str, Any], document: dict[str, Any]) -> None:
    parent = config["parent_namespaces"]
    capabilities_zero = all(
        int(document["status"].get(name, "1"), 16) == 0
        for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    )
    document["reaper"] = _reaper()
    document["descendant"] = _descendant()
    write_probe = Path.cwd() / ".native-sandbox-probe-write"
    try:
        write_probe.write_text("ok", encoding="utf-8")
        write_probe.unlink()
        cwd_writable = True
    except OSError:
        cwd_writable = False
    document["checks"] = {
        "original_uid": os.getuid() == config["expected_uid"]
        and os.geteuid() == config["expected_uid"],
        "original_gid": os.getgid() == config["expected_gid"]
        and os.getegid() == config["expected_gid"],
        "no_new_privileges": document["status"]["NoNewPrivs"] == "1",
        "empty_capabilities": capabilities_zero,
        "private_user_ns": document["namespaces"]["user"] != parent["user"],
        "private_mount_ns": document["namespaces"]["mnt"] != parent["mnt"],
        "private_pid_ns": document["namespaces"]["pid"] != parent["pid"],
        "private_proc": not Path(f"/proc/{config['parent_pid']}").exists(),
        "private_network_ns": document["namespaces"]["net"] != parent["net"],
        "only_loopback": document["interfaces"] == ["lo"],
        "loopback_down": not bool(document["loopback"]["flags"] & 0x1),
        "network_denied": document["network"]["connected"] is False,
        "initial_payload_pid2": os.getpid() == 2 and document["identity"]["ppid"] == 1,
        "default_reaper": "bwrap" in document["reaper"],
        "descendant_not_pid1": document["descendant"]["returncode"] == 0
        and document["descendant"]["pid"] != 1
        and document["descendant"]["reaper_visible"],
        "cwd_writable": cwd_writable,
        "canary_read_denied": _canary_denied(Path(config["canary_path"]), write=False),
        "canary_write_denied": _canary_denied(Path(config["canary_path"]), write=True),
        "parent_secret_env_absent": PARENT_SECRET_ENV not in os.environ,
        "stdio_only": _stdio_only(),
    }


def run(config: dict[str, Any]) -> dict[str, Any]:
    document = _base_document(config)
    mode = config["mode"]
    if mode == "e1-net":
        _run_e1_net(config, document)
    elif mode == "e2-loopback":
        _run_e2_loopback(config, document)
    elif mode == "current-no-network":
        _run_current(config, document, no_net=False)
    elif mode == "no-net-control":
        _run_current(config, document, no_net=True)
    elif mode == "e5":
        _run_e5(config, document)
    else:
        raise ValueError(f"unknown payload mode: {mode}")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    document = run(config)
    output = Path(config["output"])
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failures = [name for name, passed in document["checks"].items() if passed is not True]
    if failures:
        print(f"payload invariant failures: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
