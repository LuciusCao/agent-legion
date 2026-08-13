"""Execution preparation for the Worker: stage one claimed execution locally.

Everything here runs before the Agent process starts: claim a stale-dir-free
workspace, download the bundle and input artifacts (through the shared
download semaphore), and render the command line. Failures raise and the
caller reports them as a failed execution via the upload queue.
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker.bundle_io import download_input_artifacts, safe_extract
from worker.claim_manifest import apply_live_manifest
from worker.host_client import Client


@dataclass(frozen=True)
class PreparedExecution:
    manifest: dict[str, Any]
    command: list[str]


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
    download_input_artifacts(client, manifest, job_dir, download_slots)
    command_spec = manifest["command_spec"]
    paths = {
        "job_dir": str(job_dir),
        "skill_dir": str(extracted / "skill"),
        "session_dir": str(session_dir),
        "session_name": f"agent-legion-{execution_id}",
        "prompt_file": str(prompt_file),
    }
    prompt_file.write_text(substitute(str(command_spec["prompt"]), paths), encoding="utf-8")
    command = [substitute(str(part), paths) for part in command_spec["command"]]
    return PreparedExecution(manifest=manifest, command=command)
