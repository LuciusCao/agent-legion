"""Minimal non-root self-map evidence for disposable measurement."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

_SELF_MAP_PROBE = r"""
import ctypes, errno, json, os, sys

os.environ.clear()
uid, gid = (int(value) for value in sys.argv[1:3])
libc = ctypes.CDLL(None, use_errno=True)
libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
read_fd, write_fd = os.pipe()
child = os.fork()
if child == 0:
    os.close(read_fd)
    def snapshot():
        status = {}
        keys = "Uid Gid CapPrm CapEff CapBnd NoNewPrivs Seccomp Seccomp_filters"
        with open("/proc/self/status", encoding="utf-8") as stream:
            for line in stream:
                key, separator, value = line.partition(":")
                if separator and key in keys.split():
                    status[key] = value.strip()
        try:
            with open("/proc/self/attr/current", encoding="utf-8") as stream:
                app_armor = stream.read().strip()
        except OSError as exc:
            app_armor = f"{type(exc).__name__}: {exc}"
        return {
            "pid": os.getpid(), "ppid": os.getppid(),
            "real_uid": os.getuid(), "effective_uid": os.geteuid(),
            "real_gid": os.getgid(), "effective_gid": os.getegid(),
            "user_namespace": os.readlink("/proc/self/ns/user"),
            "uid_map": open("/proc/self/uid_map", encoding="utf-8").read(),
            "status": status, "dumpable": libc.prctl(3, 0, 0, 0, 0),
            "app_armor_current": app_armor,
        }
    report = {"parent_pid": os.getppid(), "before_unshare": snapshot()}
    def syscall(name, function, *args):
        ctypes.set_errno(0)
        result = function(*args)
        error = ctypes.get_errno()
        report[name] = {"result": result, "errno": error, "error": os.strerror(error) if error else ""}
        return result
    try:
        if syscall("unshare", libc.unshare, 0x10000000) != 0:
            raise OSError("unshare failed")
        report["after_unshare"] = snapshot()
        if syscall("prctl_dumpable", libc.prctl, 4, 1, 0, 0, 0) != 0:
            raise OSError("dumpable failed")
        report["before_map_write"] = snapshot()
        fd = syscall("open_uid_map", libc.open, b"/proc/self/uid_map", os.O_RDWR)
        if fd < 0:
            raise OSError("open uid_map failed")
        mapping = f"{uid} {uid} 1\n".encode()
        if syscall("write_uid_map", libc.write, fd, mapping, len(mapping)) != len(mapping):
            raise OSError("write uid_map failed")
        os.close(fd)
        fd = os.open("/proc/self/setgroups", os.O_RDWR)
        os.write(fd, b"deny\n")
        os.close(fd)
        fd = os.open("/proc/self/gid_map", os.O_RDWR)
        os.write(fd, f"{gid} {gid} 1\n".encode())
        os.close(fd)
        report["after_maps"] = snapshot()
        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["failure"] = f"{type(exc).__name__}: {exc}"
    os.write(write_fd, json.dumps(report, sort_keys=True).encode())
    os.close(write_fd)
    os._exit(0 if report["success"] else 1)
os.close(write_fd)
with os.fdopen(read_fd, "rb") as stream:
    report = json.loads(stream.read())
_, status = os.waitpid(child, 0)
report["child_wait_status"] = status
report["parent_pid"] = os.getpid()
print(json.dumps(report, sort_keys=True))
"""

_TRACE = "trace=clone,clone3,fork,vfork,unshare,prctl,openat,write,setgroups"


def _compose_result(run: Any, arguments: list[str]) -> Any:
    if hasattr(run, "compose_result"):
        return run.compose_result(*arguments)
    return subprocess.run(
        ["docker", "compose", *run.compose_args, *arguments],
        cwd=Path(__file__).resolve().parents[2],
        env=run.env,
        check=True,
        capture_output=True,
        text=True,
    )


def run_self_map_evidence(run: Any) -> dict[str, Any]:
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
            _TRACE,
            "python3",
            "-c",
            _SELF_MAP_PROBE,
            uid,
            gid,
        ],
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    report["trace"] = result.stderr[-8192:]
    from scripts.stress.worker_resource_parent_userns_evidence import run_parent_write_evidence

    report["parent_write"] = run_parent_write_evidence(run)
    return report


def with_self_map_evidence(run: Any, cases: Any) -> dict[str, Any]:
    self_map = run_self_map_evidence(run)
    if not self_map.get("success"):
        return {"nonroot_self_map": self_map}
    return cases() | {"nonroot_self_map": self_map}


def wrap_evidence(run: Any) -> dict[str, Any]:
    from scripts.stress.worker_resource_bwrap_inner_evidence import capture_inner_evidence
    from scripts.stress.worker_resource_wrap_evidence import _STRACE_SUPPORT

    run.compose("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT, capture=True)
    return with_self_map_evidence(run, lambda: capture_inner_evidence(run))
