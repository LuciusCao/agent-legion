"""End-to-end stress runner for large-scale agent concurrency.

Launches the backend server, seeds jobs with the synthetic load generator, runs
the Playwright frontend stress scenario, and writes a combined report.

Example:
    uv run python scripts/stress/run_e2e_stress.py \
        --agents 100 --jobs 10000 --duration 900 --browser chromium
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Allow importing `server` when the script is executed directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
FRONTEND_DIR = PROJECT_ROOT / "frontend"

from scripts.stress._e2e_readiness import wait_for_server, wait_for_snapshot_readiness  # noqa: E402
from scripts.stress._e2e_report import E2EStressReport, write_report  # noqa: E402

logger = logging.getLogger(__name__)
STRESS_RESULTS = PROJECT_ROOT / "stress-results"


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_run_dir() -> Path:
    run_id = f"{_iso_now().replace(':', '-')}-{uuid.uuid4().hex[:8]}"
    run_dir = STRESS_RESULTS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _backend_command(port: int) -> list[str]:
    return (
        f"{sys.executable} -m uvicorn server.app.main:app "
        f"--host 127.0.0.1 --port {port} --log-level warning"
    ).split()


def _frontend_command(port: int) -> list[str]:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm not found; frontend stress cannot run")
    return [
        npm,
        "run",
        "dev",
        "--",
        "--port",
        str(port),
        "--strictPort",
    ]


def _start_backend(cmd: list[str], run_dir: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "VIDEO_HIVE_DATA_DIR": str(PROJECT_ROOT / "data" / "stress"),
        "AGENT_LEGION_ENABLE_STRESS_EVENTS": "1",
    }
    logs_dir = run_dir / "backend"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "server-stdout.log"
    stderr_path = logs_dir / "server-stderr.log"
    logger.info("Starting backend: %s", " ".join(cmd))
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        return subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )


def _start_frontend(cmd: list[str], backend_base_url: str, run_dir: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "VITE_API_TARGET": backend_base_url,
    }
    logs_dir = run_dir / "frontend-server"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "server-stdout.log"
    stderr_path = logs_dir / "server-stderr.log"
    logger.info("Starting frontend: %s", " ".join(cmd))
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        return subprocess.Popen(
            cmd,
            cwd=FRONTEND_DIR,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )


def _wait_for_frontend(frontend_base_url: str, timeout: float = 60.0) -> bool:
    import requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(frontend_base_url, timeout=2.0)
            if response.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def _run_backend_stress(
    base_url: str,
    workspace: str,
    agents: int,
    jobs: int,
    duration: int,
    event_rate: int,
    run_dir: Path,
) -> tuple[subprocess.Popen, Path, list[str]]:
    backend_results = run_dir / "backend"
    backend_results.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["VIDEO_HIVE_DATA_DIR"] = str(PROJECT_ROOT / "data" / "stress")
    env["AGENT_LEGION_ENABLE_STRESS_EVENTS"] = "1"
    cmd = [
        sys.executable,
        "scripts/stress/simulate_agents.py",
        "--workspace",
        workspace,
        "--agents",
        str(agents),
        "--jobs",
        str(jobs),
        "--event-rate",
        str(event_rate),
        "--duration",
        str(duration),
        "--base-url",
        base_url,
        "--results-dir",
        str(backend_results),
    ]
    logger.info("Starting backend stress: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc, backend_results / "backend-metrics.json", cmd


def _run_frontend_stress(
    base_url: str,
    workspace: str,
    browser: str,
    duration: int,
    run_dir: Path,
) -> tuple[Path | None, list[str], str | None]:
    frontend_results = run_dir / "frontend"
    frontend_results.mkdir(parents=True, exist_ok=True)

    npm = shutil.which("npm")
    if npm is None:
        return None, [], "npm not found; frontend stress cannot run"

    env = os.environ.copy()
    env["STRESS_BASE_URL"] = base_url
    env["STRESS_WORKSPACE"] = workspace
    env["STRESS_DURATION"] = str(duration)
    env["STRESS_BROWSER"] = browser
    env["STRESS_RESULTS_DIR"] = str(frontend_results)

    cmd = [npm, "run", "stress:workspace", "--", "--reporter=line"]
    logger.info("Running frontend stress: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, cwd=FRONTEND_DIR, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        return None, cmd, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, cmd, str(exc)

    metrics_path = frontend_results / "frontend-metrics.json"
    return (metrics_path if metrics_path.exists() else None), cmd, None


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run(
    agents: int,
    jobs: int,
    duration: int,
    event_rate: int,
    browser: str,
    workspace: str,
    keep_server: bool,
    skip_frontend: bool,
) -> int:
    run_dir = _make_run_dir()
    report = E2EStressReport(
        run_id=run_dir.name,
        started_at=_iso_now(),
    )
    logger.info("E2E stress run: %s", run_dir.name)

    backend_port = _find_free_port()
    frontend_port = _find_free_port()
    backend_base_url = f"http://127.0.0.1:{backend_port}"
    frontend_base_url = f"http://127.0.0.1:{frontend_port}"
    backend_proc: subprocess.Popen | None = None
    frontend_proc: subprocess.Popen | None = None
    stress_proc: subprocess.Popen | None = None

    backend_cmd = _backend_command(backend_port)
    frontend_cmd = _frontend_command(frontend_port)
    report.backend_command = shlex.join(backend_cmd)

    try:
        backend_proc = _start_backend(backend_cmd, run_dir)

        if not wait_for_server(backend_base_url):
            report.errors.append(
                "Backend failed to start; see "
                f"{(run_dir / 'backend' / 'server-stdout.log').relative_to(PROJECT_ROOT)} "
                "and "
                f"{(run_dir / 'backend' / 'server-stderr.log').relative_to(PROJECT_ROOT)}"
            )
        else:
            frontend_proc = _start_frontend(frontend_cmd, backend_base_url, run_dir)
            if not _wait_for_frontend(frontend_base_url):
                report.errors.append(
                    "Frontend failed to start; see "
                    f"{(run_dir / 'frontend-server' / 'server-stdout.log').relative_to(PROJECT_ROOT)} "
                    "and "
                    f"{(run_dir / 'frontend-server' / 'server-stderr.log').relative_to(PROJECT_ROOT)}"
                )

            # Start the backend simulator concurrently with the frontend stress so
            # the browser experiences live SSE traffic instead of a quiet page.
            stress_proc, backend_metrics_path, _ = _run_backend_stress(
                backend_base_url, workspace, agents, jobs, duration, event_rate, run_dir
            )
            report.backend_metrics_path = str(backend_metrics_path.relative_to(PROJECT_ROOT))

            if not wait_for_snapshot_readiness(backend_base_url, workspace, min_jobs=jobs):
                report.errors.append(
                    "Workspace snapshot did not become ready before frontend stress"
                )

            if not skip_frontend and not report.errors:
                frontend_metrics_path, frontend_cmd, frontend_error = _run_frontend_stress(
                    frontend_base_url, workspace, browser, duration, run_dir
                )
                report.frontend_command = shlex.join(frontend_cmd)
                if frontend_error is not None:
                    report.errors.append(f"Frontend stress failed: {frontend_error}")
                elif frontend_metrics_path is not None:
                    report.frontend_metrics_path = str(
                        frontend_metrics_path.relative_to(PROJECT_ROOT)
                    )

            if stress_proc is not None:
                logger.info("Waiting for backend stress to finish...")
                try:
                    returncode = stress_proc.wait(timeout=duration + 120)
                    if returncode != 0:
                        report.errors.append(f"Backend stress exited with code {returncode}")
                except subprocess.TimeoutExpired:
                    report.errors.append("Backend stress did not finish in time")
                    _terminate_process(stress_proc)
    except subprocess.CalledProcessError as exc:
        report.errors.append(f"Stress step failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"Unexpected error: {exc}")
    finally:
        if stress_proc is not None and stress_proc.poll() is None:
            _terminate_process(stress_proc)
        if frontend_proc is not None:
            _terminate_process(frontend_proc)
        if backend_proc is not None and not keep_server:
            _terminate_process(backend_proc)

        report.finished_at = _iso_now()
        report_path = run_dir / "report.md"
        write_report(report, report_path, PROJECT_ROOT)
        logger.info("Wrote report to %s", report_path)

    return 1 if report.errors else 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="End-to-end stress runner for large-scale agent concurrency."
    )
    parser.add_argument("--agents", type=int, default=100, help="Synthetic agents")
    parser.add_argument("--jobs", type=int, default=10000, help="Target jobs")
    parser.add_argument("--duration", type=int, default=900, help="Duration in seconds")
    parser.add_argument(
        "--event-rate",
        type=int,
        default=500,
        help="Total raw events per second across all agents",
    )
    parser.add_argument("--browser", default="chromium", help="Playwright browser")
    parser.add_argument("--workspace", default="ws-stress", help="Stress workspace id")
    parser.add_argument(
        "--keep-server",
        action="store_true",
        help="Keep the backend server running after the run",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip the Playwright frontend stress scenario",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run(
        agents=args.agents,
        jobs=args.jobs,
        duration=args.duration,
        event_rate=args.event_rate,
        browser=args.browser,
        workspace=args.workspace,
        keep_server=args.keep_server,
        skip_frontend=args.skip_frontend,
    )


if __name__ == "__main__":
    sys.exit(main())
