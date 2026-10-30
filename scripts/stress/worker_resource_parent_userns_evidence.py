"""Minimal parent-written user-namespace map A/B for disposable measurement."""

from __future__ import annotations

import json
from typing import Any

from scripts.stress.worker_resource_userns_evidence import _compose_result

_PARENT_WRITE_PROBE = r"""
import ctypes, json, os, select, sys

os.environ.clear()
uid, gid = (int(value) for value in sys.argv[1:3])
libc = ctypes.CDLL(None, use_errno=True)
libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
ready_read, ready_write = os.pipe()
go_read, go_write = os.pipe()
report_read, report_write = os.pipe()
child = os.fork()
if child == 0:
    for fd in (ready_read, go_write, report_read):
        os.close(fd)
    report = {"pid": os.getpid(), "ppid": os.getppid()}
    try:
        if libc.unshare(0x10000000) != 0:
            raise OSError(ctypes.get_errno(), "unshare")
        report["after_unshare"] = {
            "real_uid": os.getuid(), "effective_uid": os.geteuid(),
            "real_gid": os.getgid(), "effective_gid": os.getegid(),
            "user_namespace": os.readlink("/proc/self/ns/user"),
            "uid_map": open("/proc/self/uid_map", encoding="utf-8").read(),
        }
        if libc.prctl(4, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "dumpable")
        try:
            with open("/proc/self/attr/current", encoding="utf-8") as stream:
                report["app_armor_current"] = stream.read().strip()
        except OSError as exc:
            report["app_armor_current"] = f"{type(exc).__name__}: {exc}"
        os.write(ready_write, b"R")
        os.close(ready_write)
        ready, _, _ = select.select([go_read], [], [], 5)
        if not ready or os.read(go_read, 1) != b"G":
            raise TimeoutError("parent map synchronization")
        if libc.prctl(4, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "reset dumpable")
        report["after_maps"] = {
            "real_uid": os.getuid(), "effective_uid": os.geteuid(),
            "real_gid": os.getgid(), "effective_gid": os.getegid(),
            "uid_map": open("/proc/self/uid_map", encoding="utf-8").read(),
            "dumpable": libc.prctl(3, 0, 0, 0, 0),
        }
        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["failure"] = f"{type(exc).__name__}: {exc}"
    os.write(report_write, json.dumps(report, sort_keys=True).encode())
    os.close(report_write)
    os._exit(0 if report["success"] else 1)
for fd in (ready_write, go_read, report_write):
    os.close(fd)
parent_report = {"child_pid": child, "writes": {}}
try:
    ready, _, _ = select.select([ready_read], [], [], 5)
    if not ready or os.read(ready_read, 1) != b"R":
        raise TimeoutError("child did not reach dumpable-ready phase")
    parent_report["parent_identity"] = {
        "uid": os.getuid(), "gid": os.getgid(),
        "user_namespace": os.readlink("/proc/self/ns/user"),
    }
    try:
        with open("/proc/self/attr/current", encoding="utf-8") as stream:
            parent_report["parent_app_armor_current"] = stream.read().strip()
    except OSError as exc:
        parent_report["parent_app_armor_current"] = f"{type(exc).__name__}: {exc}"
    def write_once(name, path, contents):
        fd = libc.open(path.encode(), os.O_RDWR)
        if fd < 0:
            raise OSError(ctypes.get_errno(), f"open {path}")
        try:
            data = contents.encode()
            result = libc.write(fd, data, len(data))
            parent_report["writes"][name] = {"result": result, "errno": ctypes.get_errno()}
            if result != len(data):
                raise OSError(ctypes.get_errno(), f"write {path}")
        finally:
            os.close(fd)
    write_once("uid_map", f"/proc/{child}/uid_map", f"{uid} {uid} 1\n")
    write_once("setgroups", f"/proc/{child}/setgroups", "deny\n")
    write_once("gid_map", f"/proc/{child}/gid_map", f"{gid} {gid} 1\n")
    ns_fd = libc.open(f"/proc/{child}/ns/user".encode(), os.O_RDONLY)
    if ns_fd < 0:
        raise OSError(ctypes.get_errno(), f"open /proc/{child}/ns/user")
    parent_report["parent_open"] = {
        "fd": ns_fd, "flags": libc.fcntl(ns_fd, 1),
        "namespace": os.readlink(f"/proc/self/fd/{ns_fd}"),
    }
    if parent_report["parent_open"]["flags"] & 1:
        raise RuntimeError("namespace fd must not be close-on-exec")
    os.write(go_write, b"G")
    os.close(go_write)
    ready, _, _ = select.select([report_read], [], [], 5)
    if not ready:
        raise TimeoutError("child did not finish after map completion")
    with os.fdopen(report_read, "rb") as stream:
        child_report = json.loads(stream.read())
    _, status = os.waitpid(child, 0)
    parent_report.update(child_report)
    parent_report["child_wait_status"] = status
    validator_read, validator_write = os.pipe()
    validator = os.fork()
    if validator == 0:
        os.close(validator_read)
        result = libc.setns(ns_fd, 0x10000000)
        validation = {"result": result, "errno": ctypes.get_errno()}
        if result == 0:
            validation["namespace"] = os.readlink("/proc/self/ns/user")
        os.write(validator_write, json.dumps(validation).encode())
        os._exit(0)
    os.close(validator_write)
    with os.fdopen(validator_read, "rb") as stream:
        parent_report["setns"] = json.load(stream)
    parent_report["setns"]["wait_status"] = os.waitpid(validator, 0)[1]
    os.close(ns_fd)
    parent_report["success"] = parent_report["success"] and parent_report["setns"]["result"] == 0
except Exception as exc:
    parent_report["success"] = False
    parent_report["failure"] = f"{type(exc).__name__}: {exc}"
    try:
        os.kill(child, 9)
    except ProcessLookupError:
        pass
    os.waitpid(child, 0)
print(json.dumps(parent_report, sort_keys=True))
"""


def run_parent_write_evidence(run: Any) -> dict[str, Any]:
    uid = run.env["MEASUREMENT_WORKER_UID"]
    gid = run.env["MEASUREMENT_WORKER_GID"]
    result = _compose_result(
        run,
        [
            "exec",
            "-T",
            "--user",
            f"{uid}:{gid}",
            "worker",
            "strace",
            "-f",
            "-e",
            "trace=clone,clone3,fork,vfork,unshare,prctl,openat,write,setgroups,setns,fcntl,readlink,wait4,kill",
            "python3",
            "-c",
            _PARENT_WRITE_PROBE,
            uid,
            gid,
        ],
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    report["trace"] = result.stderr[-8192:]
    return report
