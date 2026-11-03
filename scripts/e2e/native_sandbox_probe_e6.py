"""Run the candidate-only E6 direct-bwrap matrix under the N0 service constraints."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e.native_sandbox_probe import NativeSandboxProbe  # noqa: E402
from scripts.e2e.native_sandbox_probe_e6_cleanup import run_cleanup_trials  # noqa: E402
from scripts.e2e.native_sandbox_probe_e6_contract import (  # noqa: E402
    E6_CASES,
    e6_result_passed,
    validate_e6_independence,
    validate_e6_result,
    validate_rollback,
)
from scripts.e2e.native_sandbox_probe_payload import (  # noqa: E402
    LISTENER_TOKEN,
    _read,
    _utc_now,
)

E6_PAYLOAD = Path(__file__).resolve().with_name("native_sandbox_probe_e6_payload.py")


class AbstractUnixListener:
    def __init__(self, name: str) -> None:
        self.name = name
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind("\0" + name)
        self._sock.listen()
        self._sock.settimeout(0.2)
        self._count = 0
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                connection, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                connection.sendall(LISTENER_TOKEN)
                with self._lock:
                    self._count += 1

    def count(self) -> int:
        with self._lock:
            return self._count

    def close(self) -> None:
        self._stopping.set()
        self._sock.close()
        self._thread.join(timeout=2)


class E6Probe(NativeSandboxProbe):
    def __init__(self, *, state_dir: Path, output_dir: Path, phase: str) -> None:
        super().__init__(state_dir=state_dir, output_dir=output_dir, context="n0")
        self.phase = phase
        self.nonloopback_host = self._nonloopback_host()
        self.nonloopback_listener = self._tcp_listener(self.nonloopback_host)
        self.abstract_listener = AbstractUnixListener(f"native-sandbox-probe-{os.getpid()}")

    @staticmethod
    def _nonloopback_host() -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            host = sock.getsockname()[0]
        if host.startswith("127."):
            raise RuntimeError("E6 requires a non-loopback host listener")
        return host

    @staticmethod
    def _tcp_listener(host: str):
        from scripts.e2e.native_sandbox_probe import ProbeListener

        return ProbeListener(host=host)

    def close(self) -> None:
        self.nonloopback_listener.close()
        self.abstract_listener.close()
        super().close()

    def _payload_config(self, case: str, work: Path) -> Path:
        config = {
            "case": case,
            "mode": "e6",
            "context": self.context,
            "output": str(work / "payload-evidence.json"),
            "ready_path": str(work / "payload-ready.json"),
            "release_path": str(work / "payload-release.json"),
            "parent_namespaces": self.parent_namespaces,
            "parent_pid": os.getpid(),
            "parent_apparmor": _read(Path("/proc/self/attr/current")),
            "parent_seccomp": self._baseline()["process"]["status"]["Seccomp"],
            "expected_uid": os.getuid(),
            "expected_gid": os.getgid(),
            "canary_path": str(self.canary),
            "state_dir": str(self.state_dir),
            "work_path": str(work),
            "listener_host": self.listener.host,
            "listener_port": self.listener.port,
            "nonloopback_host": self.nonloopback_host,
            "nonloopback_port": self.nonloopback_listener.port,
            "abstract_socket": self.abstract_listener.name,
        }
        path = work / "payload-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def _bwrap_prefix(self, work: Path) -> list[str]:
        command = self._bwrap(work, [], unshare_net=True, e5=True)
        command[0] = "/usr/bin/bwrap"
        return command

    def _command(self, case: str) -> tuple[list[str], Path]:
        work = self.work_dir / case
        work.mkdir(parents=True, exist_ok=True)
        config = self._payload_config(case, work)
        inner = ["/usr/bin/python3", str(E6_PAYLOAD), "--config", str(config)]
        return [*self._bwrap_prefix(work), *inner], work

    def _listener_counts(self) -> dict[str, int]:
        return {
            "loopback": self.listener.count(),
            "nonloopback": self.nonloopback_listener.count(),
            "abstract_unix": self.abstract_listener.count(),
        }

    @staticmethod
    def _main_status(pid: int) -> dict[str, str]:
        rows = {}
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if separator:
                rows[key] = value.strip()
        return {
            key: rows.get(key, "")
            for key in ("NoNewPrivs", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        }

    def _run(self, case: str, command: list[str], work: Path) -> dict[str, Any]:
        before = self._listener_counts()
        ready = work / "payload-ready.json"
        process = subprocess.Popen(
            command,
            cwd=work,
            env=self._child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            close_fds=True,
        )
        main: dict[str, Any] | None = None
        payload_started = False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = _read(Path(f"/proc/{process.pid}/attr/current"))
            if current == "bwrap (enforce)":
                main = {
                    "pid": process.pid,
                    "apparmor_current": current,
                    "status": self._main_status(process.pid),
                }
            if ready.is_file():
                payload_started = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.01)
        if payload_started:
            (work / "payload-release.json").write_text("release\n", encoding="utf-8")
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=40)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=10)
        process_group_cleaned = False
        cleanup_deadline = time.monotonic() + 2
        while time.monotonic() < cleanup_deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                process_group_cleaned = True
                break
            time.sleep(0.02)
        if not process_group_cleaned:
            os.killpg(process.pid, signal.SIGKILL)
        after = self._listener_counts()
        log = self._case_dir(case) / "command.log"
        log.write_text(stdout + stderr, encoding="utf-8")
        return {
            "returncode": process.returncode,
            "timed_out": timed_out,
            "process_group_cleaned": process_group_cleaned,
            "launcher_pid": process.pid,
            "payload_started": payload_started,
            "bwrap_main": main,
            "listener_connections_before": before["loopback"],
            "listener_connections_after": after["loopback"],
            "candidate_listeners": {
                name: {"before": before[name], "after": after[name]} for name in before
            },
            "stderr_tail": stderr[-4000:],
            "command_log": str(log.relative_to(self.output_dir)),
        }

    def _write_result(self, result: dict[str, Any]) -> None:
        path = self._case_dir(result["case"]) / "result.json"
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def execute(self, case: str) -> dict[str, Any]:
        started_epoch = time.time()
        result = {
            "schema_version": 1,
            "case": case,
            "context": self.context,
            "phase": self.phase,
            "started_at": _utc_now(),
            "started_epoch": started_epoch,
            "delivery_candidate": True,
            "diagnostic_only": False,
        }
        try:
            result["baseline"] = self._baseline()
            command, work = self._command(case)
            result["command"] = command
            result["execution"] = self._run(case, command, work)
            payload_path = work / "payload-evidence.json"
            if payload_path.is_file():
                result["payload"] = json.loads(payload_path.read_text(encoding="utf-8"))
            failures = validate_e6_result(
                case,
                result,
                require_cleanup=not (self.phase == "targeted" and case == "e6-n0-single"),
            )
            result["outcome"] = "failed" if failures else "passed"
            result["expected_failure"] = self.phase == "rollback"
            result["evidence_collected"] = True
            if failures:
                result["invariant_failures"] = failures
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            result.update(
                {
                    "outcome": "infrastructure_error",
                    "expected_failure": False,
                    "evidence_collected": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        result["ended_epoch"] = time.time()
        result["ended_at"] = _utc_now()
        self._write_result(result)
        self.results[case] = result
        return result

    def _add_cleanup(self, result: dict[str, Any]) -> None:
        work = self.work_dir / result["case"] / "cleanup"
        prefix = self._bwrap_prefix(work)
        result["execution"]["cleanup_trials"] = run_cleanup_trials(
            prefix, work, env=self._child_env()
        )
        failures = validate_e6_result(result["case"], result)
        result["outcome"] = "failed" if failures else "passed"
        result["invariant_failures"] = failures
        result["ended_epoch"] = time.time()
        result["ended_at"] = _utc_now()
        self._write_result(result)

    def run(self) -> None:
        if self.phase == "rollback":
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(E6_CASES)) as pool:
                list(pool.map(self.execute, E6_CASES))
            return
        single = self.execute(E6_CASES[0])
        if single["outcome"] != "passed":
            return
        self._add_cleanup(single)
        if not e6_result_passed(E6_CASES[0], single):
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.execute, E6_CASES[1:]))

    def summarize(self) -> dict[str, Any]:
        missing = [case for case in E6_CASES if case not in self.results]
        reasons = []
        for case in E6_CASES:
            result = self.results.get(case, {})
            outcome = result.get("outcome", "missing")
            if outcome != "passed":
                reasons.append(f"E6_NOT_PASSED:{case}:{outcome}")
            elif e6_result_passed(case, result) is False:
                reasons.append(f"E6_EVIDENCE_INVALID:{case}")
        if all(self.results.get(case, {}).get("outcome") == "passed" for case in E6_CASES):
            reasons.extend(
                f"E6_NAMESPACE_INVALID:{failure}"
                for failure in validate_e6_independence(self.results)
            )
        reasons.extend(f"CASE_NOT_RECORDED:{case}" for case in missing)
        incomplete = [
            case
            for case, result in self.results.items()
            if not result.get("evidence_collected", False)
        ]
        reasons.extend(f"CASE_INFRASTRUCTURE_ERROR:{case}" for case in incomplete)
        invalid_passed = [
            case
            for case, result in self.results.items()
            if result.get("outcome") == "passed" and not e6_result_passed(case, result)
        ]
        rollback_failures = validate_rollback(self.results) if self.phase == "rollback" else []
        summary = {
            "schema_version": 1,
            "probe": "native-sandbox-e6-direct-bwrap-candidate",
            "phase": self.phase,
            "git_sha": self.git_sha,
            "generated_at": _utc_now(),
            "harness_complete": not missing and not incomplete and not invalid_passed,
            "decision": "no-go" if reasons else "go",
            "decision_scope": "E6 direct-bwrap candidate only; production behavior is unchanged",
            "candidate_only": True,
            "production_fixed": False,
            "issues": {"39": "candidate-only", "1049": "not-assessed"},
            "reason_codes": reasons,
            "rollback_restored": not rollback_failures if self.phase == "rollback" else False,
            "rollback_failures": rollback_failures,
            "cases": [self.results[case] for case in E6_CASES if case in self.results],
            "missing_cases": missing,
        }
        (self.output_dir / "candidate-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("targeted", "rollback"), required=True)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    probe = E6Probe(state_dir=args.state_dir, output_dir=args.output_dir, phase=args.phase)
    try:
        probe.run()
        if args.summarize:
            summary = probe.summarize()
            print(
                json.dumps(
                    {
                        "decision": summary["decision"],
                        "rollback_restored": summary["rollback_restored"],
                        "reasons": summary["reason_codes"],
                    }
                )
            )
    finally:
        probe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
