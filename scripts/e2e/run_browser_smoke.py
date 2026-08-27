"""Deterministic browser smoke E2E runner (test architecture plan, Phase 4A).

Boots the real backend (uvicorn factory app, no workflow worker thread) and a
vite preview static server, then runs the Playwright smoke specs in
``frontend/e2e``. Deterministic and offline: the E2E PostgreSQL database is
recreated per run, CMS question-detail lookups are served by an in-process
stub (the ``cms-internal`` external connection is seeded to point at it), and
jobs stay queued after intake because no executor/worker is started.

Example:
    uv run python scripts/e2e/run_browser_smoke.py
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


def _seed_demo_workspace(dsn: str, vault_key: str) -> None:
    """Provision the demo workspace (id=education_video_problems_generation).

    Schema v61 removed the create-path sample-template seed, so the demo DAG,
    factory Agents, node codes and materials the smoke specs drive are seeded
    up front via the same seeder `make import-demo` uses. The skill lock step
    resolves refs via git, so the repo-shipped demo skills are first imported
    into DATA_DIR (scripts/import-demo.sh via AGENT_LEGION_DEMO_SKILLS_DIR)
    and passed as skill_root. load_settings reads AGENT_LEGION_* from
    os.environ, so the e2e overrides wrap the seed call.
    """
    from scripts.seed_demo import seed_demo
    from server.app.settings import load_settings

    skills_root = DATA_DIR / "demo-skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    imported = subprocess.run(
        [str(PROJECT_ROOT / "scripts" / "import-demo.sh")],
        cwd=PROJECT_ROOT,
        env={**os.environ, "AGENT_LEGION_DEMO_SKILLS_DIR": str(skills_root)},
        capture_output=True,
        text=True,
    )
    if imported.returncode != 0:
        raise RuntimeError(f"demo skill import failed:\n{imported.stdout}\n{imported.stderr}")

    overrides = {
        "AGENT_LEGION_SKIP_DOTENV": "1",
        "AGENT_LEGION_DATABASE_URL": dsn,
        "AGENT_LEGION_DATA_DIR": str(DATA_DIR),
        "AGENT_LEGION_VAULT_MASTER_KEY": vault_key,
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        seed_demo(load_settings(), skill_root=skills_root)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _backend_env(port: int, db_name: str, vault_key: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in _CMS_ENV_KEYS:
        env[key] = ""
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
    # Factory app keeps start_worker=False: background intake still runs, but
    # no workflow worker/sweeper threads, so nodes never execute (no LLM/pi).
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "server.app.main:create_app",
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
    backend_proc: subprocess.Popen | None = None
    frontend_proc: subprocess.Popen | None = None
    cms_stub = None
    returncode = 1

    try:
        from cryptography.fernet import Fernet

        vault_key = Fernet.generate_key().decode("utf-8")
        reset_database(db_name)
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        DATA_DIR.mkdir(parents=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        cms_stub = start_cms_stub(cms_stub_port)
        backend_proc = _start_process(
            _backend_command(backend_port),
            cwd=PROJECT_ROOT,
            env=_backend_env(backend_port, db_name, vault_key),
            log_path=backend_log,
        )
        backend_base_url = f"http://127.0.0.1:{backend_port}"
        if not _wait_for_http(f"{backend_base_url}/api/health"):
            logger.error("Backend failed to start; log tail:\n%s", _log_tail(backend_log))
            return 1
        seed_cms_connection(db_dsn(db_name), cms_base_url, vault_key)
        _seed_demo_workspace(db_dsn(db_name), vault_key)

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
        cmd = [npx, "playwright", "test", "-c", "playwright.e2e.config.ts"]
        logger.info("Running Playwright smoke: %s", " ".join(cmd))
        completed = subprocess.run(
            cmd,
            cwd=FRONTEND_DIR,
            env={**os.environ, "E2E_BASE_URL": frontend_base_url},
            check=False,
        )
        returncode = completed.returncode
    finally:
        _terminate_process(frontend_proc)
        _terminate_process(backend_proc)
        if cms_stub is not None:
            cms_stub.shutdown()
            cms_stub.server_close()
        elapsed = time.monotonic() - started
        logger.info("Total elapsed: %.1fs (results in %s)", elapsed, RESULTS_DIR)
    return returncode


if __name__ == "__main__":
    sys.exit(main())
