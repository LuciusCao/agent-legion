"""Actual Host/Worker sandbox runtime evidence for disposable measurement."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from scripts.stress.worker_resource_host_code import _bounded
from scripts.stress.worker_resource_measurement import sanitize_log_text
from scripts.stress.worker_resource_output import bounded_json
from scripts.stress.worker_resource_setuid_evidence import setuid_bwrap_strace
from scripts.stress.worker_resource_wrap_evidence import _wrap_evidence

_RUNTIME_PROBE = r"""
import json, os, stat, subprocess, sys

proc = sys.argv[1] if len(sys.argv) > 1 else "/proc"
bwrap = sys.argv[2] if len(sys.argv) > 2 else "/usr/bin/bwrap"


def read(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-512:],
        "stderr": result.stderr[-2048:],
    }


def mount(path):
    text = read(proc + "/self/mountinfo")
    if text.startswith(("FileNotFoundError:", "PermissionError:")):
        return {"error": text}
    best = None
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        if not separator or len(fields) < 6:
            continue
        point = fields[4].replace("\\040", " ")
        if path == point or path.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best[0]):
                options = fields[5].split(",") + right.split()[2].split(",")
                best = point, "nosuid" in options
    return {"mount_point": best[0], "nosuid": best[1]} if best else {"error": "no mount"}


status_text = read(proc + "/self/status")
status = {}
keys = "Uid Gid Securebits CapPrm CapInh CapAmb"
keys += " CapEff CapBnd NoNewPrivs Seccomp Seccomp_filters"
for line in status_text.splitlines():
    key, separator, value = line.partition(":")
    if separator and key in keys.split():
        status[key] = value.strip()
if not status:
    status["error"] = status_text
realpath = os.path.realpath(bwrap)
bwrap_evidence = {"realpath": realpath, "mount": mount(realpath), "tmp_mount": mount("/tmp")}
try:
    mode = os.stat(realpath).st_mode
    bwrap_evidence.update(
        {"mode": f"{stat.S_IMODE(mode):04o}", "setuid": bool(mode & stat.S_ISUID)}
    )
except OSError as exc:
    bwrap_evidence["error"] = f"{type(exc).__name__}: {exc}"
bwrap_evidence["version"] = command([realpath, "--version"])
bwrap_evidence["smoke"] = command(
    [realpath, "--unshare-all", "--die-with-parent", "/bin/true"]
)
print(
    json.dumps(
        {
            "identity": {"uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups()},
            "status": status,
            "app_armor_current": read(proc + "/self/attr/current").strip(),
            "bwrap": bwrap_evidence,
        },
        sort_keys=True,
    )
)
"""


def _compose_security(run: Any) -> dict[str, Any]:
    document = json.loads(run.compose("config", "--format", "json", capture=True))
    result = {}
    for service in ("host", "worker"):
        config = document["services"][service]
        result[service] = {
            "cap_add": config.get("cap_add", []),
            "security_opt": config.get("security_opt", []),
            "privileged": config.get("privileged", False),
            "user": config.get("user", ""),
        }
    return result


def _inspect_security(container: str) -> dict[str, Any]:
    output = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .HostConfig.CapAdd}}|{{json .HostConfig.SecurityOpt}}",
            container,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    cap_add, security_opt = (json.loads(value) or [] for value in output.split("|"))
    return {"cap_add": cap_add, "security_opt": security_opt}


def _process_evidence(run: Any, service: str) -> dict[str, Any]:
    command = ["exec", "-T", service]
    if service == "host":
        command += [
            "setpriv",
            f"--reuid={run.env['MEASUREMENT_HOST_UID']}",
            f"--regid={run.env['MEASUREMENT_HOST_GID']}",
            "--clear-groups",
        ]
    command += ["python3", "-c", _RUNTIME_PROBE]
    evidence = json.loads(run.compose(*command, capture=True))
    if not isinstance(evidence, dict):
        raise RuntimeError(f"invalid {service} runtime probe shape")
    return evidence


def write_host_code_runtime_evidence(run: Any, iteration_dir: Path) -> None:
    evidence = {"compose": _compose_security(run), "containers": {}, "wrap": _wrap_evidence(run)}
    for service in ("host", "worker"):
        container = run.compose("ps", "-q", service, capture=True).strip()
        if not container or "\n" in container:
            raise RuntimeError(f"expected one {service} container")
        container_evidence = {
            "inspect": _inspect_security(container),
            "process": _process_evidence(run, service),
        }
        if service == "host":
            container_evidence["setuid_strace"] = setuid_bwrap_strace(run)
        evidence["containers"][service] = container_evidence
    text = bounded_json(evidence, 65536)
    (iteration_dir / "host-code-runtime.json").write_text(
        sanitize_log_text(text, run.secrets) + "\n", encoding="utf-8"
    )


def write_host_code_runtime_evidence_safely(run: Any, iteration_dir: Path) -> None:
    try:
        write_host_code_runtime_evidence(run, iteration_dir)
    except Exception as exc:
        (iteration_dir / "host-code-runtime-error.txt").write_text(
            _bounded(sanitize_log_text(f"{type(exc).__name__}: {exc}", run.secrets), 65536) + "\n",
            encoding="utf-8",
        )
