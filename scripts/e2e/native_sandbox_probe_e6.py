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
from scripts.e2e.native_sandbox_probe_e6_listeners import (  # noqa: E402
    AbstractUnixListener,
    abstract_control,
    control_connection,
    positive_controls,
)
from scripts.e2e.native_sandbox_probe_payload import _read, _utc_now  # noqa: E402

E6_PAYLOAD = Path(__file__).resolve().with_name("native_sandbox_probe_e6_payload.py")


class E6Probe(NativeSandboxProbe):
    def __init__(self, *, state_dir: Path, output_dir: Path, phase: str) -> None:
        super().__init__(state_dir=state_dir, output_dir=output_dir, context="n0")
        self.phase = phase
        self._expectations: dict[str, dict[str, Any]] = {}
        self._case_listeners: dict[str, dict[str, Any]] = {}
        self.nonloopback_host = self._nonloopback_host()

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

    def _new_listeners(self, case: str) -> dict[str, Any]:
        listeners = {}
        try:
            listeners["loopback"] = self._tcp_listener("127.0.0.1")
            listeners["nonloopback"] = self._tcp_listener(self.nonloopback_host)
            listeners["abstract_unix"] = AbstractUnixListener(
                f"native-sandbox-probe-{os.getpid()}-{case}"
            )
        except (OSError, RuntimeError):
            for listener in listeners.values():
                listener.close()
            raise
        self._case_listeners[case] = listeners
        return listeners

    def _close_case_listeners(self, case: str) -> None:
        for listener in self._case_listeners.pop(case, {}).values():
            listener.close()

    def close(self) -> None:
        for case in list(self._case_listeners):
            self._close_case_listeners(case)
        super().close()

    def _payload_config(self, case: str, work: Path) -> Path:
        listeners = self._new_listeners(case)
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
            "listener_host": listeners["loopback"].host,
            "listener_port": listeners["loopback"].port,
            "nonloopback_host": self.nonloopback_host,
            "nonloopback_port": listeners["nonloopback"].port,
            "abstract_socket": listeners["abstract_unix"].name,
        }
        path = work / "payload-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        self._expectations[case] = dict(config)
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

    def _listener_counts(self, case: str) -> dict[str, int]:
        return {name: listener.count() for name, listener in self._case_listeners[case].items()}

    @staticmethod
    def _control_connection(host: str, port: int) -> dict[str, Any]:
        return control_connection(host, port)

    def _abstract_control(self, case: str) -> dict[str, Any]:
        return abstract_control(self._case_listeners[case]["abstract_unix"].name)

    def _positive_controls(self, case: str) -> dict[str, dict[str, Any]]:
        return positive_controls(
            self._case_listeners[case],
            tcp_attempt=self._control_connection,
            abstract_attempt=lambda: self._abstract_control(case),
        )

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
        initial = self._listener_counts(case)
        before_controls = self._positive_controls(case)
        candidate_before = self._listener_counts(case)
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
        candidate_after = self._listener_counts(case)
        after_controls = self._positive_controls(case)
        final = self._listener_counts(case)
        log = self._case_dir(case) / "command.log"
        log.write_text(stdout + stderr, encoding="utf-8")
        return {
            "returncode": process.returncode,
            "timed_out": timed_out,
            "process_group_cleaned": process_group_cleaned,
            "launcher_pid": process.pid,
            "payload_started": payload_started,
            "bwrap_main": main,
            "listener_connections_before": candidate_before["loopback"],
            "listener_connections_after": candidate_after["loopback"],
            "candidate_listeners": {
                name: {
                    "before": initial[name],
                    "after": final[name],
                    "before_control": before_controls[name],
                    "candidate_before": candidate_before[name],
                    "candidate_after": candidate_after[name],
                    "after_control": after_controls[name],
                }
                for name in initial
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
            result["expectations"] = self._expectations[case]
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
        except (
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            result.update(
                {
                    "outcome": "infrastructure_error",
                    "expected_failure": False,
                    "evidence_collected": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            self._close_case_listeners(case)
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
