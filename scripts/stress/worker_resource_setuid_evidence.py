"""Syscall evidence for the non-root setuid bubblewrap transition."""

from __future__ import annotations

import json
from typing import Any

_SETUID_STRACE_PROBE = r"""
import json, subprocess, sys

uid, gid, bwrap = sys.argv[1:4]
command = [
    "strace",
    "-f",
    "-e",
    "trace=capset,unshare,setuid,setgid,prctl",
    "-s",
    "256",
    "setpriv",
    f"--reuid={uid}",
    f"--regid={gid}",
    "--clear-groups",
    bwrap,
    "--unshare-all",
    "--die-with-parent",
    "/bin/true",
]
try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    evidence = {
        "returncode": result.returncode,
        "stdout": result.stdout[-512:],
        "stderr": result.stderr[-8192:],
    }
except (OSError, subprocess.TimeoutExpired) as exc:
    evidence = {"error": f"{type(exc).__name__}: {exc}"}
print(json.dumps(evidence, sort_keys=True))
"""


def setuid_bwrap_strace(run: Any) -> dict[str, Any]:
    evidence = json.loads(
        run.compose(
            "exec",
            "-T",
            "host",
            "python3",
            "-c",
            _SETUID_STRACE_PROBE,
            run.env["MEASUREMENT_HOST_UID"],
            run.env["MEASUREMENT_HOST_GID"],
            "/usr/bin/bwrap",
            capture=True,
        )
    )
    if not isinstance(evidence, dict):
        raise RuntimeError("invalid setuid strace probe shape")
    return evidence
