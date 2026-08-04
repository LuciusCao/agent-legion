"""Frontend stress step for the end-to-end stress runner.

Split out of run_e2e_stress.py to keep that module inside its architecture
budget while the Phase 4C auth wiring (STRESS_SESSION_COOKIE) was added.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def run_frontend_stress(
    base_url: str,
    workspace: str,
    browser: str,
    duration: int,
    run_dir: Path,
    session_cookie: str = "",
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
    # Workspace APIs require an authenticated session; the browser context
    # injects this cookie before opening the page (stress/workspaceStress.spec.ts).
    env["STRESS_SESSION_COOKIE"] = session_cookie

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
