"""Fault-injection cleanup trials for the disposable direct-bwrap candidate."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

NAMESPACES = ("user", "mnt", "pid", "net")


def _wait_file(path: Path, process: subprocess.Popen, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.01)
    return False


def _children(pid: int) -> list[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        return [int(value) for value in path.read_text(encoding="utf-8").split()]
    except (OSError, ValueError):
        return []


def _process_tree(pid: int) -> list[int]:
    observed = {pid}
    pending = [pid]
    while pending:
        for child in _children(pending.pop()):
            if child not in observed:
                observed.add(child)
                pending.append(child)
    return sorted(observed)


def _namespace_paths(pids: list[int]) -> list[str]:
    return [f"/proc/{pid}/ns/{name}" for pid in pids for name in NAMESPACES]


def _missing_paths(paths: list[str]) -> bool:
    return all(not Path(path).exists() for path in paths)


def _processes_gone(pids: list[int], namespace_paths: list[str], timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _missing_paths([f"/proc/{pid}" for pid in pids]) and _missing_paths(namespace_paths):
            return True
        time.sleep(0.02)
    return _missing_paths([f"/proc/{pid}" for pid in pids]) and _missing_paths(namespace_paths)


def _group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def _wait_group(pgid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _group_gone(pgid):
            return True
        time.sleep(0.02)
    return _group_gone(pgid)


def _sleep_command(prefix: list[str], ready: Path) -> list[str]:
    return [
        *prefix,
        "/bin/sh",
        "-c",
        'printf "%s\\n" "$$" > "$1"; exec /bin/sleep 60',
        "sh",
        str(ready),
    ]


def _result(mode: str, launcher_pid: int, observed: list[int], paths: list[str]) -> dict[str, Any]:
    processes_reaped = _processes_gone(observed, paths)
    return {
        "mode": mode,
        "launcher_pid": launcher_pid,
        "observed_pids": observed,
        "namespace_paths": paths,
        "processes_reaped": processes_reaped,
        "namespaces_released": _missing_paths(paths),
        "process_group_cleaned": _wait_group(launcher_pid),
    }


def run_signal_or_timeout(
    prefix: list[str], work: Path, mode: str, *, env: dict[str, str]
) -> dict[str, Any]:
    ready = work / f"{mode}-ready"
    process = subprocess.Popen(
        _sleep_command(prefix, ready),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    observed: list[int] = []
    paths: list[str] = []
    expected_signal_used = False
    try:
        if not _wait_file(ready, process):
            return {
                "mode": mode,
                "launcher_pid": process.pid,
                "observed_pids": observed,
                "namespace_paths": paths,
                "expected_signal_used": False,
                "processes_reaped": False,
                "namespaces_released": False,
                "process_group_cleaned": False,
                "error": "cleanup payload did not become ready",
            }
        observed = _process_tree(process.pid)
        paths = _namespace_paths(observed)
        if mode == "signal":
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
            expected_signal_used = _processes_gone(observed, paths, timeout=3)
        else:
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                expected_signal_used = True
    finally:
        if not expected_signal_used and mode == "signal":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
    result = _result(mode, process.pid, observed, paths)
    result["expected_signal_used"] = expected_signal_used
    return result


def parent_death_launcher(ready: Path, command: list[str]) -> int:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    ready.write_text(json.dumps({"bwrap_pid": process.pid}) + "\n", encoding="utf-8")
    return process.wait()


def run_parent_death(prefix: list[str], work: Path, *, env: dict[str, str]) -> dict[str, Any]:
    launcher_ready = work / "parent-death-launcher.json"
    payload_ready = work / "parent-death-ready"
    command = _sleep_command(prefix, payload_ready)
    process = subprocess.Popen(
        [
            "/usr/bin/python3",
            str(Path(__file__).resolve()),
            "--parent-death-launcher",
            "--ready",
            str(launcher_ready),
            "--command-json",
            json.dumps(command),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    observed: list[int] = []
    paths: list[str] = []
    expected_signal_used = False
    if _wait_file(launcher_ready, process) and _wait_file(payload_ready, process):
        observed = _process_tree(process.pid)
        paths = _namespace_paths(observed)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        expected_signal_used = _processes_gone(observed, paths)
    result = _result("parent-death", process.pid, observed, paths)
    if not expected_signal_used:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        result["process_group_cleaned"] = _wait_group(process.pid)
        result["processes_reaped"] = _processes_gone(observed, paths)
        result["namespaces_released"] = _missing_paths(paths)
    result["expected_signal_used"] = expected_signal_used
    return result


def run_cleanup_trials(
    prefix: list[str], work: Path, *, env: dict[str, str]
) -> list[dict[str, Any]]:
    work.mkdir(parents=True, exist_ok=True)
    return [
        run_signal_or_timeout(prefix, work, "signal", env=env),
        run_signal_or_timeout(prefix, work, "timeout", env=env),
        run_parent_death(prefix, work, env=env),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-death-launcher", action="store_true")
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--command-json")
    args = parser.parse_args()
    if not args.parent_death_launcher or args.ready is None or not args.command_json:
        parser.error("--parent-death-launcher requires --ready and --command-json")
    return parent_death_launcher(args.ready, json.loads(args.command_json))


if __name__ == "__main__":
    raise SystemExit(main())
