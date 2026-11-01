"""Run the M140 native Ubuntu diagnostics without changing production sandbox code."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e.native_sandbox_probe_payload import LISTENER_TOKEN, PARENT_SECRET_ENV  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PAYLOAD = SCRIPT_DIR / "native_sandbox_probe_payload.py"
VELITES = REPO_ROOT / "data" / "bin" / "velites"
N0_USER = "agent-legion-sandbox-probe"
N0_CASES = (
    "e0-n0-current-no-network",
    "e0-n0-bwrap-no-net-control",
    "e1-n0-user-namespace",
    "e1-n0-user-net-namespace",
    "e2-n0-private-loopback",
    "e3-n0-raw-netlink-trace",
    "e5-n0-single",
    "e5-n0-concurrent-a",
    "e5-n0-concurrent-b",
)
EXTERNAL_CASES = (
    "e0-runuser-current-no-network",
    "e0-runuser-bwrap-no-net-control",
    "e4-nnp-yes-cap-empty",
    "e4-nnp-no-cap-empty",
    "e4-nnp-yes-cap-default",
    "e4-nnp-no-cap-default",
)
ALL_CASES = (
    "e0-n0-current-no-network",
    "e0-runuser-current-no-network",
    "e0-n0-bwrap-no-net-control",
    "e0-runuser-bwrap-no-net-control",
    "e1-n0-user-namespace",
    "e1-n0-user-net-namespace",
    "e2-n0-private-loopback",
    "e3-n0-raw-netlink-trace",
    "e4-nnp-yes-cap-empty",
    "e4-nnp-no-cap-empty",
    "e4-nnp-yes-cap-default",
    "e4-nnp-no-cap-default",
    "e5-n0-single",
    "e5-n0-concurrent-a",
    "e5-n0-concurrent-b",
)
CAPABILITY_KEYS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"unavailable: {type(exc).__name__}: {exc.errno}"


def _proc_status() -> dict[str, str]:
    rows = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            rows[key] = value.strip()
    return rows


def _namespace(name: str) -> str:
    return os.readlink(Path("/proc/self/ns") / name)


def _command_output(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    return (completed.stdout + completed.stderr).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ProbeListener:
    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self._sock.settimeout(0.2)
        self.host, self.port = self._sock.getsockname()
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


class NativeSandboxProbe:
    def __init__(self, *, state_dir: Path, output_dir: Path, context: str) -> None:
        self.state_dir = state_dir
        self.output_dir = output_dir
        self.context = context
        self.cases_dir = output_dir / "cases"
        self.work_dir = state_dir / "work" / context
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.git_sha = (state_dir / "probe-git-sha").read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", self.git_sha):
            raise RuntimeError("probe git SHA is missing or invalid")
        self._verify_identity()
        os.environ[PARENT_SECRET_ENV] = "native-sandbox-probe-parent-canary"
        self.parent_namespaces = {name: _namespace(name) for name in ("user", "mnt", "pid", "net")}
        self.canary = state_dir / "parent-canary"
        self.canary.write_text("native-sandbox-probe-canary\n", encoding="utf-8")
        self.canary.chmod(0o600)
        self.listener = ProbeListener()
        self.results: dict[str, dict[str, Any]] = {}
        if context == "n0":
            ready = {
                "pid": os.getpid(),
                "uid": os.getuid(),
                "git_sha": self.git_sha,
                "cgroup": Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines(),
            }
            (output_dir / "n0-ready.json").write_text(
                json.dumps(ready, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

    def _verify_identity(self) -> None:
        if os.getuid() == 0 or os.geteuid() == 0 or os.environ.get("USER") != N0_USER:
            raise RuntimeError("native probe must use its fixed non-root account")
        if Path(os.environ.get("HOME", "")).resolve() != self.state_dir.resolve():
            raise RuntimeError("native probe requires the independent service HOME")
        if self.context == "n0":
            status = _proc_status()
            if status.get("NoNewPrivs") != "1" or any(
                int(status.get(name, "1"), 16) != 0 for name in CAPABILITY_KEYS
            ):
                raise RuntimeError("N0 requires NoNewPrivileges and empty capabilities")

    def close(self) -> None:
        self.listener.close()

    def _baseline(self) -> dict[str, Any]:
        bwrap = Path("/usr/bin/bwrap")
        bwrap_stat = bwrap.stat()
        profiles = Path("/sys/kernel/security/apparmor/profiles")
        profile_text = _read(profiles)
        policy = {"path": str(profiles), "status": profile_text}
        if not profile_text.startswith("unavailable:"):
            policy = {
                "path": str(profiles),
                "profile_count": len(profile_text.splitlines()),
                "sha256": hashlib.sha256(profile_text.encode()).hexdigest(),
            }
        aa_enabled = subprocess.run(
            ["/usr/sbin/aa-enabled"], capture_output=True, text=True, timeout=10, check=False
        )
        return {
            "captured_at": _utc_now(),
            "context": self.context,
            "git_sha": self.git_sha,
            "os_release": {
                key: _read(Path("/etc/os-release"))
                .split(f"{key}=", 1)[-1]
                .splitlines()[0]
                .strip('"')
                for key in ("ID", "VERSION_ID")
            },
            "bubblewrap": {
                "package_version": _command_output(
                    ["dpkg-query", "--show", "--showformat=${Version}", "bubblewrap"]
                ),
                "binary_version": _command_output([str(bwrap), "--version"]),
                "path": str(bwrap),
                "mode": stat.filemode(bwrap_stat.st_mode),
                "uid": bwrap_stat.st_uid,
                "gid": bwrap_stat.st_gid,
                "file_capabilities": _command_output(["getcap", str(bwrap)]),
            },
            "velites": {
                "path": str(VELITES),
                "version": _command_output([str(VELITES), "--version"]),
                "sha256": _sha256(VELITES),
            },
            "process": {
                "uid": os.getuid(),
                "gid": os.getgid(),
                "status": {
                    key: value
                    for key, value in _proc_status().items()
                    if key in {"NoNewPrivs", "Seccomp", *CAPABILITY_KEYS}
                },
                "apparmor_current": _read(Path("/proc/self/attr/current")),
                "namespaces": dict(self.parent_namespaces),
            },
            "apparmor": {
                "module_enabled": _read(Path("/sys/module/apparmor/parameters/enabled")),
                "aa_enabled_returncode": aa_enabled.returncode,
                "policy": policy,
            },
            "read_only_sysctls": {
                "unprivileged_userns_clone": _read(
                    Path("/proc/sys/kernel/unprivileged_userns_clone")
                ),
                "max_user_namespaces": _read(Path("/proc/sys/user/max_user_namespaces")),
                "max_net_namespaces": _read(Path("/proc/sys/user/max_net_namespaces")),
            },
        }

    def _case_dir(self, case: str) -> Path:
        path = self.cases_dir / case
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _payload_config(self, case: str, mode: str, work: Path) -> Path:
        config = {
            "case": case,
            "mode": mode,
            "context": self.context,
            "output": str(work / "payload-evidence.json"),
            "parent_namespaces": self.parent_namespaces,
            "parent_pid": os.getpid(),
            "expected_uid": os.getuid(),
            "expected_gid": os.getgid(),
            "canary_path": str(self.canary),
        }
        if mode in {"current-no-network", "no-net-control", "e5"}:
            config.update(
                {"listener_host": self.listener.host, "listener_port": self.listener.port}
            )
        path = work / "payload-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def _payload_command(self, case: str, mode: str, work: Path) -> list[str]:
        config = self._payload_config(case, mode, work)
        return ["/usr/bin/python3", str(PAYLOAD), "--config", str(config)]

    def _bwrap(self, work: Path, inner: list[str], *, unshare_net: bool, e5: bool) -> list[str]:
        argv = ["bwrap", "--die-with-parent"]
        if e5:
            argv.extend(
                [
                    "--unshare-user",
                    "--uid",
                    str(os.getuid()),
                    "--gid",
                    str(os.getgid()),
                    "--cap-drop",
                    "ALL",
                ]
            )
        argv.append("--unshare-pid")
        if unshare_net:
            argv.append("--unshare-net")
        for root in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt"):
            if Path(root).is_dir():
                argv.extend(["--ro-bind", root, root])
        argv.extend(["--ro-bind", str(SCRIPT_DIR), str(SCRIPT_DIR)])
        resolv = Path("/etc/resolv.conf")
        if resolv.exists():
            target = resolv.resolve()
            if not target.is_relative_to(Path("/etc")):
                argv.extend(["--ro-bind-try", str(target), str(target)])
        argv.extend(
            [
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                "/tmp",
                "--bind",
                str(work),
                str(work),
                "--",
            ]
        )
        argv.extend(inner)
        return argv

    def _current(self, case: str, work: Path) -> list[str]:
        inner = self._payload_command(case, "current-no-network", work)
        return [
            str(VELITES),
            "sandbox",
            "wrap",
            "--cwd",
            str(work),
            "--allow-read",
            str(SCRIPT_DIR),
            "--",
            *inner,
        ]

    def _command(self, case: str) -> tuple[list[str], Path]:
        work = self.work_dir / case
        work.mkdir(parents=True, exist_ok=True)
        if case.endswith("current-no-network") or case.startswith("e4-"):
            return self._current(case, work), work
        if case.endswith("bwrap-no-net-control"):
            inner = self._payload_command(case, "no-net-control", work)
            return self._bwrap(work, inner, unshare_net=False, e5=False), work
        if case == "e1-n0-user-namespace":
            return ["unshare", "--user", "--map-root-user", "--", "/usr/bin/true"], work
        if case == "e1-n0-user-net-namespace":
            inner = self._payload_command(case, "e1-net", work)
            return ["unshare", "--user", "--map-root-user", "--net", "--", *inner], work
        if case == "e2-n0-private-loopback":
            inner = self._payload_command(case, "e2-loopback", work)
            script = "ip addr add 127.0.0.1/8 dev lo && ip link set lo up && exec " + " ".join(
                inner
            )
            return [
                "unshare",
                "--user",
                "--map-root-user",
                "--net",
                "--",
                "/bin/sh",
                "-c",
                script,
            ], work
        if case == "e3-n0-raw-netlink-trace":
            trace = self._case_dir(case) / "raw-strace.log"
            current = self._current(case, work)
            return [
                "strace",
                "-f",
                "-qq",
                "-s",
                "256",
                "-yy",
                "-o",
                str(trace),
                "-e",
                "trace=%network,clone,clone3,unshare,setns,prctl,capset",
                *current,
            ], work
        if case.startswith("e5-"):
            inner = self._payload_command(case, "e5", work)
            bwrap = self._bwrap(work, inner, unshare_net=False, e5=True)
            return ["unshare", "--user", "--map-root-user", "--net", "--", *bwrap], work
        raise ValueError(f"unknown case: {case}")

    def _child_env(self) -> dict[str, str]:
        return {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "USER", "LOGNAME")
            if key in os.environ
        }

    def _run(self, case: str, command: list[str], work: Path) -> dict[str, Any]:
        extra_fd = os.open("/dev/null", os.O_RDONLY)
        before = self.listener.count()
        process = None
        timed_out = False
        try:
            process = subprocess.Popen(
                command,
                cwd=work,
                env=self._child_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                pass_fds=(extra_fd,),
            )
            try:
                stdout, stderr = process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=10)
        finally:
            os.close(extra_fd)
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
        after = self.listener.count()
        log = self._case_dir(case) / "command.log"
        log.write_text(stdout + stderr, encoding="utf-8")
        return {
            "returncode": process.returncode,
            "timed_out": timed_out,
            "process_group_cleaned": process_group_cleaned,
            "launcher_pid": process.pid,
            "listener_connections_before": before,
            "listener_connections_after": after,
            "stderr_tail": stderr[-4000:],
            "command_log": str(log.relative_to(self.output_dir)),
        }

    def execute(self, case: str) -> dict[str, Any]:
        started_epoch = time.time()
        result = {
            "schema_version": 1,
            "case": case,
            "context": self.context,
            "started_at": _utc_now(),
            "started_epoch": started_epoch,
            "delivery_candidate": case.startswith("e5-"),
            "diagnostic_only": case.startswith("e4-"),
        }
        try:
            result["baseline"] = self._baseline()
            command, work = self._command(case)
            result["command"] = command
            result["execution"] = self._run(case, command, work)
            payload_path = work / "payload-evidence.json"
            if payload_path.is_file():
                result["payload"] = json.loads(payload_path.read_text(encoding="utf-8"))
            execution = result["execution"]
            expected_connections = 1 if case.endswith("bwrap-no-net-control") else 0
            connection_delta = (
                execution["listener_connections_after"] - execution["listener_connections_before"]
            )
            payload_checks = result.get("payload", {}).get("checks", {})
            checks_passed = all(value is True for value in payload_checks.values())
            passed = (
                execution["returncode"] == 0
                and not execution["timed_out"]
                and execution["process_group_cleaned"]
                and connection_delta == expected_connections
                and checks_passed
            )
            result["outcome"] = "passed" if passed else "failed"
            result["expected_failure"] = (
                case.endswith("current-no-network")
                or case.startswith("e4-")
                or case == "e3-n0-raw-netlink-trace"
            )
            result["evidence_collected"] = True
            if not execution["process_group_cleaned"]:
                result["invariant_failures"] = ["process_group_cleanup"]
            elif connection_delta != expected_connections:
                result["invariant_failures"] = ["listener_connection_count"]
            elif payload_checks and not checks_passed:
                result["invariant_failures"] = [
                    name for name, value in payload_checks.items() if value is not True
                ]
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
        path = self._case_dir(case) / "result.json"
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.results[case] = result
        return result

    def skip(self, case: str, prerequisite: str) -> dict[str, Any]:
        now = time.time()
        result = {
            "schema_version": 1,
            "case": case,
            "context": self.context,
            "started_at": _utc_now(),
            "started_epoch": now,
            "ended_at": _utc_now(),
            "ended_epoch": now,
            "outcome": "skipped",
            "delivery_candidate": case.startswith("e5-"),
            "diagnostic_only": False,
            "prerequisite": prerequisite,
            "prerequisite_outcome": self.results.get(prerequisite, {}).get("outcome", "missing"),
            "baseline": self._baseline(),
        }
        path = self._case_dir(case) / "result.json"
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.results[case] = result
        return result

    def load_existing(self) -> None:
        for case in EXTERNAL_CASES:
            path = self._case_dir(case) / "result.json"
            if path.is_file():
                self.results[case] = json.loads(path.read_text(encoding="utf-8"))

    def summarize(self) -> dict[str, Any]:
        self.load_existing()
        missing = [case for case in ALL_CASES if case not in self.results]
        e5_cases = [case for case in ALL_CASES if case.startswith("e5-")]
        reasons = []
        for prerequisite in ("e0-n0-bwrap-no-net-control", "e1-n0-user-net-namespace"):
            if self.results.get(prerequisite, {}).get("outcome") != "passed":
                reasons.append(f"E5_PREREQUISITE_FAILED:{prerequisite}")
        for case in e5_cases:
            outcome = self.results.get(case, {}).get("outcome", "missing")
            if outcome != "passed":
                reasons.append(f"E5_NOT_PASSED:{case}:{outcome}")
        reasons.extend(f"CASE_NOT_RECORDED:{case}" for case in missing)
        incomplete = [
            case
            for case, result in self.results.items()
            if not result.get("evidence_collected", True)
        ]
        reasons.extend(f"CASE_INFRASTRUCTURE_ERROR:{case}" for case in incomplete)
        decision = "no-go" if reasons else "go"
        summary = {
            "schema_version": 1,
            "probe": "native-sandbox-network-probe",
            "git_sha": self.git_sha,
            "generated_at": _utc_now(),
            "harness_complete": not missing and not incomplete,
            "decision": decision,
            "decision_scope": "E5 probe candidate only; production behavior is unchanged",
            "production_fixed": False,
            "issues": {"39": decision, "1049": decision},
            "reason_codes": reasons,
            "e5_prerequisites": {
                case: self.results.get(case, {}).get("outcome", "missing")
                for case in ("e0-n0-bwrap-no-net-control", "e1-n0-user-net-namespace")
            },
            "cases": [self.results[case] for case in ALL_CASES if case in self.results],
            "missing_cases": missing,
        }
        (self.output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    probe = NativeSandboxProbe(
        state_dir=args.state_dir, output_dir=args.output_dir, context=args.context
    )
    try:
        cases = args.cases.split(",")
        e5_selected = [case for case in cases if case.startswith("e5-")]
        for case in cases:
            if case.startswith("e5-"):
                continue
            if (
                case == "e2-n0-private-loopback"
                and probe.results.get("e1-n0-user-net-namespace", {}).get("outcome") != "passed"
            ):
                probe.skip(case, "e1-n0-user-net-namespace")
                continue
            probe.execute(case)
        prerequisites_met = all(
            probe.results.get(case, {}).get("outcome") == "passed"
            for case in ("e0-n0-bwrap-no-net-control", "e1-n0-user-net-namespace")
        )
        if e5_selected and not prerequisites_met:
            for case in e5_selected:
                prerequisite = (
                    "e0-n0-bwrap-no-net-control"
                    if probe.results.get("e0-n0-bwrap-no-net-control", {}).get("outcome")
                    != "passed"
                    else "e1-n0-user-net-namespace"
                )
                probe.skip(case, prerequisite)
        elif e5_selected:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(e5_selected)) as pool:
                list(pool.map(probe.execute, e5_selected))
            payloads = [probe.results[case].get("payload", {}) for case in e5_selected]
            for namespace in ("user", "mnt", "pid", "net"):
                observed = [
                    payload.get("namespaces", {}).get(namespace) for payload in payloads if payload
                ]
                if len(observed) != len(set(observed)):
                    for case in e5_selected:
                        probe.results[case]["outcome"] = "failed"
                        probe.results[case].setdefault("invariant_failures", []).append(
                            f"concurrent_{namespace}_namespace_independence"
                        )
                        path = probe._case_dir(case) / "result.json"
                        path.write_text(
                            json.dumps(probe.results[case], indent=2, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
        if args.summarize:
            summary = probe.summarize()
            print(json.dumps({"decision": summary["decision"], "reasons": summary["reason_codes"]}))
    finally:
        probe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
