"""Outer/inner bwrap evidence for disposable measurement."""

from __future__ import annotations

import json
from typing import Any

_STRACE_SUPPORT = (
    "command -v strace || "
    "(apt-get update -qq && apt-get install -y -qq --no-install-recommends strace)"
)

_WRAP_PROBE = r"""
import json, os, subprocess, sys

uid, gid = (int(value) for value in sys.argv[1:3])


def read(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


status_text = read("/proc/self/status")
status = {}
keys = "Uid Gid Securebits CapPrm CapInh CapAmb CapEff CapBnd NoNewPrivs Seccomp Seccomp_filters"
for line in status_text.splitlines():
    key, separator, value = line.partition(":")
    if separator and key in keys.split():
        status[key] = value.strip()
outer = {
    "requested_identity": {"uid": int(uid), "gid": int(gid)},
    "identity": {"uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups()},
    "status": status,
    "namespaces": {
        name: os.readlink(f"/proc/self/ns/{name}")
        for name in ("user", "pid", "net", "mnt")
    },
    "app_armor_current": read("/proc/self/attr/current").strip(),
}
trace = "trace=clone,clone3,capset,unshare,setuid,setgid,prctl,mount,umount2,setns,socket,bind,sendto,recvmsg,openat,write,setgroups"
no_network = [
    "strace", "-f", "-e", trace,
    "/app/data/bin/velites", "sandbox", "wrap", "--cwd", "/tmp", "--", "/bin/true",
]
allow_network = [*no_network[:-4], "--allow-network", *no_network[-4:]]
launcher = "import ctypes,os,subprocess,sys; os.environ.clear(); assert ctypes.CDLL(None).prctl(4,1,0,0,0) == 0; raise SystemExit(subprocess.run(sys.argv[1:], check=False).returncode)"
reset = "import ctypes,os; os.environ.clear(); assert ctypes.CDLL(None).prctl(4,0,0,0,0) == 0; os.execv('/bin/true',['/bin/true'])"


def run_case(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        inner = {
            "returncode": result.returncode,
            "stdout": result.stdout[-1024:],
            "stderr": result.stderr[-4096:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        inner = {"error": f"{type(exc).__name__}: {exc}"}
    inner["command"] = command
    return inner


cases = {"allow_network": run_case(allow_network), "no_network": run_case(no_network)}
if uid != 0:
    explicit = [
        "strace", "-f", "-e", trace, "bwrap", "--die-with-parent",
        "--unshare-user", "--unshare-pid", "--unshare-net",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--", "/bin/true",
    ]
    cases["explicit_userns_no_network"] = run_case(explicit)
    for name, command in (("allow_network", allow_network), ("no_network", no_network)):
        cases[f"{name}_dumpable_first"] = run_case([*command[:4], "python3", "-c", launcher, *command[4:-1], "python3", "-c", reset])
print(json.dumps({"outer": outer, "cases": cases}, sort_keys=True))
"""


def _probe(run: Any, uid: str, gid: str) -> dict[str, Any]:
    command = (
        "exec",
        "-T",
        "--user",
        f"{uid}:{gid}",
        "worker",
        "python3",
        "-c",
        _WRAP_PROBE,
        uid,
        gid,
    )
    output = run.compose(*command, capture=True)
    return json.loads(output.strip().splitlines()[-1])


def _wrap_evidence(run: Any) -> dict[str, Any]:
    uid = run.env["MEASUREMENT_WORKER_UID"]
    gid = run.env["MEASUREMENT_WORKER_GID"]
    run.compose("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT, capture=True)
    return {"root": _probe(run, "0", "0"), "nonroot": _probe(run, uid, gid)}
