"""Deterministic browser smoke E2E runner (test architecture plan, Phase 4).

Seeds the databases first, then boots the real backend (uvicorn factory app,
workflow worker/sweeper threads on) and a vite preview static server, then
runs the Playwright smoke specs in ``frontend/e2e``. Seeding is
seed-before-serve (PR #240): the backend's WorkflowWorkerThread builds its
scan_entries snapshot at startup and direct-DB seeding triggers no reload,
so the workspaces must exist before the backend boots; the e2e workspace's
dispatch resume lands after health because every app startup resets all
workspaces to paused (the demo workspace stays paused, so the original
smoke specs keep their queued-job behavior). Deterministic and offline: the
E2E PostgreSQL database is recreated per run, CMS question-detail lookups
are served by an in-process stub (the ``cms-internal`` external connection
is seeded to point at it), and the main-flow spec's velites Agent node
talks to an in-process stub LLM gateway (OpenAI SSE) claimed through a
standalone Worker process (``worker/executor.py``).

Example:
    uv run python scripts/e2e/run_browser_smoke.py [playwright spec filter...]
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.e2e._cms_stub import seed_cms_connection, start_cms_stub  # noqa: E402
from scripts.e2e._database import db_dsn, e2e_database_name, reset_database  # noqa: E402
from scripts.e2e._demo_seed import seed_demo_workspace  # noqa: E402
from scripts.e2e._llm_stub import StubGateway  # noqa: E402
from scripts.e2e._main_flow_seed import resume_main_flow_workspace  # noqa: E402
from scripts.e2e._worker import (  # noqa: E402
    ensure_velites_binary,
    prepare_main_flow_runtime,
    start_worker,
)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
RESULTS_DIR = FRONTEND_DIR / "e2e-results"
DATA_DIR = PROJECT_ROOT / "data" / "e2e-smoke"

logger = logging.getLogger(__name__)

# Mirrors tests/conftest.py: blank every CMS/remote credential so the backend
# can never reach real external services during smoke runs.
_CMS_ENV_KEYS = (
    "CMS_BASE_URL",
    "CMS_TOKEN",
    "CMS_APP_ID",
    "CMS_NONCE",
    "CMS_SECRET",
    "CMS_TOKEN_URL",
    "BASECMS_BASE_URL",
    "BASECMS_TOKEN",
    "BASECMS_APP_ID",
    "BASECMS_NONCE",
    "BASECMS_SECRET",
    "BASECMS_TOKEN_URL",
    "AGENT_LEGION_CMS_TOKEN",
    "AGENT_LEGION_REMOTE_WORKER_TOKEN",
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _backend_env(port: int, db_name: str, vault_key: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in _CMS_ENV_KEYS:
        env[key] = ""
    # Host code-pool sandbox: velites is probed on PATH (shutil.which).
    env["PATH"] = f"{PROJECT_ROOT / 'data' / 'bin'}:{env.get('PATH', '')}"
    env.update(
        {
            "AGENT_LEGION_SKIP_DOTENV": "1",
            "AGENT_LEGION_DATABASE_URL": db_dsn(db_name),
            "AGENT_LEGION_DATA_DIR": str(DATA_DIR),
            "AGENT_LEGION_VAULT_MASTER_KEY": vault_key,
        }
    )
    return env


def _backend_command(port: int) -> list[str]:
    # Factory wrapper flips start_worker=True: sweeper + workflow worker
    # (Host code pool + agent dispatch enqueue) run inside the backend.
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "scripts.e2e._backend_factory:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]


def _frontend_bundle_fresh() -> bool:
    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if not dist_index.exists():
        return False
    dist_mtime = dist_index.stat().st_mtime
    candidates = [
        FRONTEND_DIR / "index.html",
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "vite.config.ts",
    ]
    candidates.extend(p for p in (FRONTEND_DIR / "src").rglob("*") if p.is_file())
    return all(p.stat().st_mtime <= dist_mtime for p in candidates if p.exists())


def _build_frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm not found; frontend build cannot run")
    logger.info("Building frontend bundle (npm run build:bundle)")
    subprocess.run([npm, "run", "build:bundle"], cwd=FRONTEND_DIR, check=True)


def _start_process(
    cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Starting: %s", " ".join(cmd))
    with log_path.open("wb") as log:
        return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=log)


def _wait_for_http(url: str, timeout: float = 90.0) -> bool:
    import requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=2.0).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _log_tail(path: Path, lines: int = 40) -> str:
    if not path.exists():
        return f"{path} missing"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    started = time.monotonic()
    db_name = e2e_database_name(PROJECT_ROOT)
    backend_port = _find_free_port()
    frontend_port = _find_free_port()
    cms_stub_port = _find_free_port()
    frontend_base_url = f"http://127.0.0.1:{frontend_port}"
    cms_base_url = f"http://127.0.0.1:{cms_stub_port}/v2"
    backend_log = RESULTS_DIR / "backend.log"
    frontend_log = RESULTS_DIR / "frontend-preview.log"
    worker_log = RESULTS_DIR / "worker.log"
    backend_proc: subprocess.Popen | None = None
    frontend_proc: subprocess.Popen | None = None
    worker_proc: subprocess.Popen | None = None
    cms_stub = None
    llm_stub: StubGateway | None = None
    returncode = 1

    try:
        from cryptography.fernet import Fernet

        vault_key = Fernet.generate_key().decode("utf-8")
        reset_database(db_name)
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        DATA_DIR.mkdir(parents=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        ensure_velites_binary()
        cms_stub = start_cms_stub(cms_stub_port)
        backend_base_url = f"http://127.0.0.1:{backend_port}"
        # Seed BEFORE the backend boots (seed-before-serve, PR #240): the
        # backend's WorkflowWorkerThread builds its scan_entries snapshot at
        # startup and direct-DB seeding triggers no reload, so every workspace
        # must already exist by then. JobQueries initializes the schema itself,
        # and the demo seed (first) creates the tables seed_cms_connection
        # writes into.
        seed_demo_workspace(db_dsn(db_name), vault_key, DATA_DIR, PROJECT_ROOT)
        llm_stub, worker_yaml = prepare_main_flow_runtime(
            dsn=db_dsn(db_name), data_dir=DATA_DIR, backend_base_url=backend_base_url
        )
        seed_cms_connection(db_dsn(db_name), cms_base_url, vault_key)
        backend_proc = _start_process(
            _backend_command(backend_port),
            cwd=PROJECT_ROOT,
            env=_backend_env(backend_port, db_name, vault_key),
            log_path=backend_log,
        )
        if not _wait_for_http(f"{backend_base_url}/api/health"):
            logger.error("Backend failed to start; log tail:\n%s", _log_tail(backend_log))
            return 1
        # Every app startup resets ALL workspaces to paused; the resume must
        # land after the backend is healthy (the demo workspace stays paused).
        resume_main_flow_workspace(db_dsn(db_name))
        worker_proc = start_worker(worker_yaml, worker_log)

        if not _frontend_bundle_fresh():
            _build_frontend()
        else:
            logger.info("frontend/dist is up to date; skipping build")
        npm = shutil.which("npm")
        if npm is None:
            logger.error("npm not found; frontend preview cannot run")
            return 1
        frontend_proc = _start_process(
            [npm, "run", "preview", "--", "--port", str(frontend_port), "--strictPort"],
            cwd=FRONTEND_DIR,
            env={**os.environ, "VITE_API_TARGET": backend_base_url},
            log_path=frontend_log,
        )
        if not _wait_for_http(frontend_base_url):
            logger.error(
                "Frontend preview failed to start; log tail:\n%s",
                _log_tail(frontend_log),
            )
            return 1

        npx = shutil.which("npx")
        if npx is None:
            logger.error("npx not found; Playwright cannot run")
            return 1
        # Extra args pass through to Playwright (spec filter, e.g.
        # `run_browser_smoke.py smoke-main-flow` runs one spec — the
        # deterministic check that the main flow does not depend on another
        # spec's publish-triggered scan_entries reload, PR #240).
        cmd = [npx, "playwright", "test", "-c", "playwright.e2e.config.ts", *sys.argv[1:]]
        logger.info("Running Playwright smoke: %s", " ".join(cmd))
        completed = subprocess.run(
            cmd,
            cwd=FRONTEND_DIR,
            env={**os.environ, "E2E_BASE_URL": frontend_base_url},
            check=False,
        )
        returncode = completed.returncode
    finally:
        _terminate_process(worker_proc)
        _terminate_process(frontend_proc)
        _terminate_process(backend_proc)
        if cms_stub is not None:
            cms_stub.shutdown()
            cms_stub.server_close()
        if llm_stub is not None:
            llm_stub.close()
        elapsed = time.monotonic() - started
        logger.info("Total elapsed: %.1fs (results in %s)", elapsed, RESULTS_DIR)
    return returncode


if __name__ == "__main__":
    sys.exit(main())
