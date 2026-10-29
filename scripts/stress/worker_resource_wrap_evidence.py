"""Outer/inner bwrap evidence for disposable measurement."""

from __future__ import annotations

import json
from typing import Any

_WRAP_PROBE = r"""
import json, os, stat, subprocess, sys

uid, gid = (int(value) for value in sys.argv[1:3])


def read(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


subprocess.run(
    [
        "sh", "-c",
        "command -v strace || (apt-get update -qq && apt-get install -y -qq --no-install-recommends strace)",
    ],
    check=True,
)
os.setgid(gid)
os.setuid(uid)
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
command = [
    "strace", "-f", "-e",
    "trace=capset,unshare,setuid,setgid,prctl,mount,umount2,setns,socket,bind,sendto,recvmsg",
    "/app/data/bin/velites", "sandbox", "wrap", "--cwd", "/tmp", "--", "/bin/true",
]
try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    inner = {
        "returncode": result.returncode,
        "stdout": result.stdout[-2048:],
        "stderr": result.stderr[-16384:],
    }
except (OSError, subprocess.TimeoutExpired) as exc:
    inner = {"error": f"{type(exc).__name__}: {exc}"}
inner["command"] = command
print(json.dumps({"outer": outer, "inner_bwrap": inner}, sort_keys=True))
"""


def _wrap_evidence(run: Any) -> dict[str, Any]:
    output = run.compose(
        "run",
        "--rm",
        "--no-deps",
        "--user",
        "0:0",
        "--entrypoint",
        "python3",
        "worker",
        "-c",
        _WRAP_PROBE,
        run.env["MEASUREMENT_WORKER_UID"],
        run.env["MEASUREMENT_WORKER_GID"],
        capture=True,
    )
    return json.loads(output.strip().splitlines()[-1])
