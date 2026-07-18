#!/usr/bin/env python3
# scripts/remote/worker.py
"""Remote worker agent for Agent Legion distributed execution.

Stdlib-only single file: copy it to any machine with python3 and the pi CLI,
then run it pointing at the video-hive server over the tailnet:

    python3 worker.py --server http://100.x.y.z:8000 --token "$REMOTE_WORKER_TOKEN" \
        --worker-id mac-mini --name "Mac mini" --slots 65 \
        --capabilities generate_key_info,review_key_info
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

HEARTBEAT_INTERVAL_SECONDS = 15.0
PROMPT_INSTRUCTION = "Execute the attached node instructions."
KILL_GRACE_SECONDS = 5.0


# ---- pure builders (must mirror server/app/workflows/pi_prompt.py and pi_command_builder.py) ----


def render_prompt(manifest: dict[str, Any], job_dir: Path, skill_dir: Path) -> str:
    lines = [
        "Execute the loaded node skill for this Video Hive workflow job.",
        "",
        f"Job ID: {manifest['job_id']}",
        f"Node: {manifest['node_key']}",
        f"Working directory: {job_dir}",
        f"Skill directory: {skill_dir}",
        f"Validator script: {skill_dir / 'scripts' / 'validate_output.py'}",
        "",
        "Declared inputs:",
        *(f"- {item}" for item in manifest["inputs"]),
        "",
        "Required outputs:",
        *(f"- {item}" for item in manifest["expected_outputs"]),
        "",
        (
            "Write required outputs directly into the working directory. "
            "Never write outputs into the run/session directory (runs/); "
            "all declared outputs must live at the top level of the working directory. "
            "Do not modify inputs or create undeclared root-level artifacts. "
            "Finish after all required outputs are written and correct."
        ),
    ]
    additional = str(manifest.get("additional_prompt", "")).strip()
    if additional:
        lines.extend(["", "Additional node instructions:", additional])
    return "\n".join(lines) + "\n"


def build_command(
    manifest: dict[str, Any],
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
) -> list[str]:
    pi = manifest["pi"]
    cmd: list[str] = [
        str(pi.get("binary") or "pi"),
        "--mode",
        "json",
        "--session-dir",
        str(session_dir),
        "--name",
        session_name,
        "--no-context-files",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-skills",
        "--skill",
        str(skill_dir),
        "--tools",
        ",".join(manifest["tools"]),
        "--approve",
    ]
    for flag, key in (("--provider", "provider"), ("--model", "model"), ("--thinking", "thinking")):
        value = str(pi.get(key) or "")
        if value:
            cmd.extend([flag, value])
    cmd.extend([f"@{prompt_file}", PROMPT_INSTRUCTION])
    return cmd


def detect_model_error(events_file: Path) -> str | None:
    """Mirror pi_runner._detect_model_error's reading of real pi events.

    Pi can exit 0 even when the upstream model request fails; the events file
    then carries assistant messages with an ``errorMessage``. Field access here
    must stay identical to the server-side scanner.
    """
    if not events_file.is_file():
        return None
    try:
        with events_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # message_start / message_end / turn_end wrap the assistant msg
                msg = event.get("message") or {}
                if not isinstance(msg, dict):
                    turn_end = event.get("turn_end") or {}
                    msg = turn_end.get("message") if isinstance(turn_end, dict) else {}
                if isinstance(msg, dict) and msg.get("errorMessage"):
                    return str(msg["errorMessage"])

                # message_update events nest under assistantMessageEvent
                assistant_event = event.get("assistantMessageEvent") or {}
                if isinstance(assistant_event, dict):
                    msg = assistant_event.get("message") or {}
                    if isinstance(msg, dict) and msg.get("errorMessage"):
                        return str(msg["errorMessage"])
    except Exception:
        return None
    return None


# ---- archive handling (same safety rules as server remote_bundle.py) ----


def _safe_members(tar: tarfile.TarFile):
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe path in archive: {member.name!r}")
        if member.islnk() or member.issym():
            raise ValueError(f"links are not allowed in archives: {member.name!r}")
        yield member


def extract_bundle(bundle_path: Path, work_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Extract a server bundle. Returns (job_dir, skill_dir, manifest)."""
    job_dir = work_dir / "job"
    skill_dir = work_dir / "skill"
    job_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "r:gz") as tar:
        for member in _safe_members(tar):
            name = PurePosixPath(member.name)
            if str(name) == "manifest.json":
                continue
            if "skill" in name.parts[:1]:
                target = work_dir / name
            elif name.parts and name.parts[0] == "inputs":
                target = job_dir / PurePosixPath(*name.parts[1:])
            else:
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    with tarfile.open(bundle_path, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
    return job_dir, skill_dir, manifest


def pack_result_archive(
    archive_path: Path, *, job_dir: Path, run_dir: Path, expected_outputs: list[str]
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for name in expected_outputs:
            src = job_dir / name
            if src.is_file():
                tar.add(src, arcname=name)
        if run_dir.is_dir():
            tar.add(run_dir, arcname=str(run_dir.relative_to(job_dir)))


# ---- server client ----


class WorkerClient:
    def __init__(self, server: str, token: str, worker_id: str, timeout: float = 30.0) -> None:
        self._server = server.rstrip("/")
        self._token = token
        self._worker_id = worker_id
        self._timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            f"{self._server}{path}",
            data=body,
            method=method,
            headers={
                "X-Worker-Token": self._token,
                "X-Worker-Id": self._worker_id,
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def register(self, name: str, capabilities: list[str], slots: int) -> None:
        status, data = self._request(
            "POST",
            "/api/remote/register",
            body=json.dumps(
                {
                    "worker_id": self._worker_id,
                    "name": name,
                    "capabilities": capabilities,
                    "slots": slots,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        if status != 204:
            raise RuntimeError(f"register failed: HTTP {status}: {data[:200]!r}")

    def claim(self, capabilities: list[str]) -> dict[str, Any] | None:
        status, data = self._request(
            "POST",
            "/api/remote/claim",
            body=json.dumps({"worker_id": self._worker_id, "capabilities": capabilities}).encode(
                "utf-8"
            ),
            headers={"Content-Type": "application/json"},
        )
        if status == 204:
            return None
        if status != 200:
            raise RuntimeError(f"claim failed: HTTP {status}: {data[:200]!r}")
        return json.loads(data.decode("utf-8"))

    def download_bundle(self, claim: dict[str, Any], dest: Path) -> None:
        status, data = self._request("GET", claim["bundle_url"])
        if status != 200:
            raise RuntimeError(f"bundle download failed: HTTP {status}")
        dest.write_bytes(data)

    def heartbeat(self, execution_id: str) -> bool:
        status, _ = self._request("POST", f"/api/remote/executions/{execution_id}/heartbeat")
        if status == 204:
            return True
        if status == 409:
            return False
        # Transient server/proxy error (e.g. 5xx): let the heartbeat loop's
        # exception path keep the run alive and retry next interval.
        raise urllib.error.URLError(f"unexpected heartbeat status: {status}")

    def report(self, execution_id: str, metadata: dict[str, Any], archive: Path) -> None:
        status, data = self._request(
            "POST",
            f"/api/remote/executions/{execution_id}/result",
            body=archive.read_bytes(),
            headers={"X-Remote-Result": json.dumps(metadata)},
        )
        if status != 204:
            raise RuntimeError(f"report failed: HTTP {status}: {data[:200]!r}")


# ---- execution ----


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=KILL_GRACE_SECONDS)


def run_execution(
    client: WorkerClient, claim: dict[str, Any], work_root: Path
) -> tuple[dict[str, Any], Path | None]:
    """Run one claimed execution. Returns (result_metadata, archive_path|None).

    archive_path is None when the claim was lost mid-run (server requeued or
    cancelled it) — the caller must NOT report in that case.
    """
    execution_id = claim["execution_id"]
    work_dir = work_root / execution_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    bundle_path = work_dir / "bundle.tar.gz"
    client.download_bundle(claim, bundle_path)
    job_dir, skill_dir, manifest = extract_bundle(bundle_path, work_dir)

    run_token = manifest["run_token"]
    run_dir = job_dir / "runs" / manifest["node_key"] / run_token
    session_dir = run_dir / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = run_dir / "prompt.md"
    prompt_file.write_text(render_prompt(manifest, job_dir, skill_dir), encoding="utf-8")
    session_name = f"{manifest['job_id']}:{manifest['node_key']}:{run_token}"
    command = build_command(
        manifest,
        skill_dir=skill_dir,
        session_dir=session_dir,
        session_name=session_name,
        prompt_file=prompt_file,
    )
    pi = manifest["pi"]
    env = {**os.environ, **{str(k): str(v) for k, v in pi.get("environment", {}).items()}}
    # Never hand the server credential to the LLM-driven agent: it could read
    # the token via its bash tool and exfiltrate it into events.jsonl.
    env.pop("REMOTE_WORKER_TOKEN", None)

    events_file = run_dir / "events.jsonl"
    stderr_file = run_dir / "stderr.log"
    start_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    claim_lost = threading.Event()

    with events_file.open("w") as stdout_fh, stderr_file.open("w") as stderr_fh:
        proc = subprocess.Popen(
            command,
            stdout=stdout_fh,
            stderr=stderr_fh,
            cwd=str(job_dir),
            env=env,
            start_new_session=True,
        )

        def heartbeat_loop() -> None:
            while proc.poll() is None and not claim_lost.is_set():
                # Transient network failure (laptop asleep, tailnet blip): keep
                # the run alive and retry next interval; the server sweeps only
                # after claim_timeout_seconds without contact.
                with contextlib.suppress(Exception):
                    if not client.heartbeat(execution_id):
                        claim_lost.set()
                        return
                claim_lost.wait(HEARTBEAT_INTERVAL_SECONDS)

        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        exit_code = 0
        error_message = ""
        deadline = time.monotonic() + float(pi.get("timeout_seconds", 600))
        while True:
            if claim_lost.is_set():
                _terminate_process_group(proc)
                heartbeat_thread.join(timeout=5)
                return (
                    {
                        "status": "cancelled",
                        "exit_code": -1,
                        "error_message": "claim lost during execution",
                        "command": command,
                        "skill_version": manifest["skill_version"],
                    },
                    None,
                )
            poll = proc.poll()
            if poll is not None:
                exit_code = poll
                break
            if time.monotonic() > deadline:
                _terminate_process_group(proc)
                exit_code = -1
                error_message = f"Pi session timed out after {pi.get('timeout_seconds', 600)}s"
                break
            time.sleep(0.2)
        heartbeat_thread.join(timeout=5)

    if exit_code == 0:
        model_error = detect_model_error(events_file)
        if model_error:
            exit_code = 1
            error_message = f"Pi model call failed: {model_error}"
        else:
            missing = [o for o in manifest["expected_outputs"] if not (job_dir / o).is_file()]
            if missing:
                exit_code = 1
                error_message = f"Missing outputs after Pi run: {', '.join(missing)}"

    if exit_code == 0:
        validator = skill_dir / "scripts" / "validate_output.py"
        if validator.is_file():
            try:
                val_proc = subprocess.run(
                    [sys.executable, str(validator), str(job_dir)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if val_proc.returncode != 0:
                    exit_code = 1
                    error_message = f"Output validation failed: {val_proc.stderr.strip()}"
            except Exception as exc:
                exit_code = 1
                error_message = f"Validator error: {exc}"

    end_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    run_meta = {
        "node_key": manifest["node_key"],
        "run_id": run_token,
        "command": command,
        "start_time": start_time,
        "end_time": end_time,
        "exit_code": exit_code,
        "model": {
            "provider": pi.get("provider", ""),
            "model": pi.get("model", ""),
            "thinking": pi.get("thinking", ""),
        },
        "inputs": manifest["inputs"],
        "outputs": manifest["expected_outputs"],
        "skill": str(skill_dir),
        "skill_version": manifest["skill_version"],
        "error_message": error_message,
    }
    (run_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2))

    archive_path = work_dir / "result.tar.gz"
    pack_result_archive(
        archive_path,
        job_dir=job_dir,
        run_dir=run_dir,
        expected_outputs=manifest["expected_outputs"],
    )
    metadata = {
        "status": "completed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "error_message": error_message,
        "command": command,
        "skill_version": manifest["skill_version"],
    }
    return metadata, archive_path


# ---- main loop ----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Legion remote worker")
    parser.add_argument("--server", required=True, help="e.g. http://100.x.y.z:8000")
    parser.add_argument("--token", default=os.environ.get("REMOTE_WORKER_TOKEN", ""))
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--name", default="")
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument("--capabilities", required=True, help="comma-separated capability names")
    parser.add_argument("--work-dir", default="./remote-worker-work")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args(argv)

    if not args.token:
        parser.error("--token or REMOTE_WORKER_TOKEN is required")
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    if not capabilities:
        parser.error("--capabilities must list at least one capability")

    client = WorkerClient(args.server, args.token, args.worker_id)
    work_root = Path(args.work_dir)
    work_root.mkdir(parents=True, exist_ok=True)

    client.register(args.name or args.worker_id, capabilities, args.slots)
    print(f"[worker] registered as {args.worker_id} with {args.slots} slots", flush=True)

    while True:
        try:
            claim = client.claim(capabilities)
        except Exception as exc:
            print(f"[worker] poll error: {exc}; retrying", flush=True)
            time.sleep(args.poll_interval * 2)
            continue
        if claim is None:
            time.sleep(args.poll_interval)
            continue
        print(f"[worker] claimed {claim['execution_id']} ({claim['capability']})", flush=True)
        try:
            metadata, archive = run_execution(client, claim, work_root)
            if archive is not None:
                client.report(claim["execution_id"], metadata, archive)
                print(
                    f"[worker] reported {claim['execution_id']}: {metadata['status']}", flush=True
                )
            else:
                print(
                    f"[worker] claim lost for {claim['execution_id']}; skipped report", flush=True
                )
        except Exception as exc:
            print(f"[worker] execution error for {claim['execution_id']}: {exc}", flush=True)
        finally:
            shutil.rmtree(work_root / claim["execution_id"], ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
