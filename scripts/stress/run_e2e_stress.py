"""End-to-end stress runner for large-scale agent concurrency.

Launches the backend server, seeds jobs with the synthetic load generator, runs
the Playwright frontend stress scenario, and writes a combined report.

Example:
    uv run python scripts/stress/run_e2e_stress.py \
        --agents 100 --jobs 10000 --duration 900 --browser chromium
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Allow importing `server` when the script is executed directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
FRONTEND_DIR = PROJECT_ROOT / "frontend"

logger = logging.getLogger(__name__)
STRESS_RESULTS = PROJECT_ROOT / "stress-results"


@dataclass
class E2EStressReport:
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    backend_command: str = ""
    frontend_command: str = ""
    backend_metrics_path: str | None = None
    frontend_metrics_path: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def _wait_for_server(base_url: str, timeout: float = 60.0) -> bool:
    import requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/api/workspaces", timeout=2)
            if response.status_code < 500:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def _start_backend(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["VIDEO_HIVE_DATA_DIR"] = str(PROJECT_ROOT / "data" / "stress")
    env["AGENT_LEGION_ENABLE_STRESS_EVENTS"] = "1"
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "server.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    logger.info("Starting backend: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run_backend_stress(
    base_url: str,
    workspace: str,
    agents: int,
    jobs: int,
    duration: int,
    event_rate: int,
    run_dir: Path,
) -> Path:
    backend_results = run_dir / "backend"
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
    logger.info("Running backend stress: %s", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, check=True)
    return backend_results / "backend-metrics.json"


def _run_frontend_stress(
    base_url: str,
    workspace: str,
    browser: str,
    duration: int,
    run_dir: Path,
) -> Path | None:
    frontend_results = run_dir / "frontend"
    frontend_results.mkdir(parents=True, exist_ok=True)

    npm = shutil.which("npm")
    if npm is None:
        logger.warning("npm not found; skipping frontend stress")
        return None

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
        logger.warning("Frontend stress failed or is not configured: %s", exc)
        return None

    metrics_path = frontend_results / "frontend-metrics.json"
    return metrics_path if metrics_path.exists() else None


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
) -> int:
    run_dir = _make_run_dir()
    report = E2EStressReport(
        run_id=run_dir.name,
        started_at=_iso_now(),
    )
    logger.info("E2E stress run: %s", run_dir.name)

    backend_port = _find_free_port()
    base_url = f"http://127.0.0.1:{backend_port}"
    backend_proc: subprocess.Popen | None = None

    try:
        backend_proc = _start_backend(backend_port)
        if not _wait_for_server(base_url):
            report.errors.append("Backend failed to start")
            return 1

        backend_metrics_path = _run_backend_stress(
            base_url, workspace, agents, jobs, duration, event_rate, run_dir
        )
        report.backend_metrics_path = str(backend_metrics_path.relative_to(PROJECT_ROOT))

        frontend_metrics_path = _run_frontend_stress(
            base_url, workspace, browser, duration, run_dir
        )
        if frontend_metrics_path is not None:
            report.frontend_metrics_path = str(frontend_metrics_path.relative_to(PROJECT_ROOT))
    except subprocess.CalledProcessError as exc:
        report.errors.append(f"Stress step failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"Unexpected error: {exc}")
        return 1
    finally:
        if backend_proc is not None and not keep_server:
            _terminate_process(backend_proc)

    report.finished_at = _iso_now()
    report_path = run_dir / "report.md"
    _write_report(report, run_dir, report_path)
    logger.info("Wrote report to %s", report_path)
    return 0


def _write_report(report: E2EStressReport, run_dir: Path, report_path: Path) -> None:
    backend_metrics: dict[str, Any] = {}
    if report.backend_metrics_path:
        backend_file = PROJECT_ROOT / report.backend_metrics_path
        if backend_file.exists():
            backend_metrics = json.loads(backend_file.read_text(encoding="utf-8"))

    frontend_metrics: dict[str, Any] = {}
    if report.frontend_metrics_path:
        frontend_file = PROJECT_ROOT / report.frontend_metrics_path
        if frontend_file.exists():
            frontend_metrics = json.loads(frontend_file.read_text(encoding="utf-8"))

    lines = [
        "# Large Scale Agent Concurrency Stress Report\n",
        f"**Run ID:** {report.run_id}\n",
        f"**Started:** {report.started_at}\n",
        f"**Finished:** {report.finished_at}\n",
        "\n## Backend Metrics\n",
        "```json\n",
        json.dumps(backend_metrics, indent=2, default=str),
        "\n```\n",
        "\n## Frontend Metrics\n",
        "```json\n",
        json.dumps(frontend_metrics, indent=2, default=str),
        "\n```\n",
    ]
    if report.errors:
        lines.append("\n## Errors\n")
        for error in report.errors:
            lines.append(f"- {error}\n")
    report_path.write_text("".join(lines), encoding="utf-8")


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
    )


if __name__ == "__main__":
    sys.exit(main())
