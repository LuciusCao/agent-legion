"""Execution preparation for the Worker: stage one claimed execution locally.

Everything here runs before the Agent process starts: claim a stale-dir-free
workspace, download the bundle and input artifacts (through the shared
download semaphore), and render the command line. Failures raise and the
caller reports them as a failed execution via the upload queue.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from worker.claim_manifest import apply_live_manifest
from worker.host_client import Client


@dataclass(frozen=True)
class PreparedExecution:
    manifest: dict[str, Any]
    command: list[str]


def safe_extract(archive: Path, destination: Path) -> dict[str, Any]:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.islnk() or member.issym():
                raise ValueError(f"unsafe Agent bundle member: {member.name!r}")
        tar.extractall(destination, filter="data")
    return json.loads((destination / "manifest.json").read_text(encoding="utf-8"))


def substitute(value: str, paths: dict[str, str]) -> str:
    for key, replacement in paths.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def prepare_execution(
    client: Client,
    claim: dict[str, Any],
    execution_dir: Path,
    download_slots: threading.Semaphore,
) -> PreparedExecution:
    execution_id = str(claim["execution_id"])
    node_key = str(claim["node_key"])
    bundle = execution_dir / "bundle.tar.gz"
    extracted = execution_dir / "bundle"
    job_dir = execution_dir / "job"
    run_dir = job_dir / "runs" / node_key / "worker"
    session_dir = run_dir / "session"
    prompt_file = run_dir / "prompt.md"
    if execution_dir.exists():
        # Stale dir from a crashed run or a re-claimed execution: drop it.
        print(f"removing stale execution dir for {execution_id}", flush=True)
        shutil.rmtree(execution_dir, ignore_errors=True)
    execution_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    session_dir.mkdir(parents=True)
    with download_slots:
        client.download(str(claim["bundle_url"]), bundle)
    manifest = apply_live_manifest(safe_extract(bundle, extracted), claim)
    for name, ref in manifest.get("input_artifacts", {}).items():
        digest = str(ref).split(":", 1)[-1]
        target = job_dir / PurePosixPath(str(name))
        with download_slots:
            client.download(f"/api/artifacts/{digest}", target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"artifact digest mismatch: {name}")
    command_spec = manifest["command_spec"]
    prompt_file.write_text(str(command_spec["prompt"]), encoding="utf-8")
    paths = {
        "job_dir": str(job_dir),
        "skill_dir": str(extracted / "skill"),
        "session_dir": str(session_dir),
        "session_name": f"agent-legion-{execution_id}",
        "prompt_file": str(prompt_file),
    }
    command = [substitute(str(part), paths) for part in command_spec["command"]]
    return PreparedExecution(manifest=manifest, command=command)
