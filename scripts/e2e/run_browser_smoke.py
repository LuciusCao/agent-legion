"""Deterministic browser smoke E2E runner (test architecture plan, Phase 4A).

Boots the real backend (uvicorn factory app, no workflow worker thread) and a
vite preview static server, then runs the Playwright smoke specs in
``frontend/e2e``. Deterministic and offline: the E2E PostgreSQL database is
recreated per run, CMS question-detail lookups are served by an in-process
stub (CMS_BASE_URL override), and jobs stay queued after intake because no
executor/worker is started.

Example:
    uv run python scripts/e2e/run_browser_smoke.py
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
    "AGENT_LEGION_CMS_TOKEN_GEN_SECRET",
    "AGENT_LEGION_REMOTE_WORKER_TOKEN",
)


def _e2e_database_name() -> str:
    # Dedicated per-worktree database, mirroring tests/postgres_support.py but
    # with its own prefix so E2E runtime state never mixes with pytest state.
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", PROJECT_ROOT.name).lower()
    return f"agent_legion_e2e_{slug}"


def _reset_database(db_name: str) -> None:
    """Reset the E2E database to empty state.

    Creates the database on first use; afterwards wipes all tables with
    TRUNCATE (same isolation style as tests/conftest.py). DROP/CREATE was
    measurably slower (tens of seconds on a loaded machine) because the drop
    forces buffer flushes, while TRUNCATE stays sub-second.
    """
    import psycopg
    from psycopg import sql

    logger.info("Resetting E2E database %s", db_name)
    with psycopg.connect("postgresql://127.0.0.1:5432/postgres", autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (db_name,))
        exists = conn.execute("select 1 from pg_database where datname = %s", (db_name,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("create database {}").format(sql.Identifier(db_name)))
            return
    with psycopg.connect(f"postgresql://127.0.0.1:5432/{db_name}", autocommit=True) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "select tablename from pg_tables where schemaname = 'public'"
            ).fetchall()
        ]
        if not tables:
            return
        conn.execute(
            sql.SQL("truncate table {} restart identity cascade").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in tables)
            )
        )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_cms_stub(port: int) -> http.server.ThreadingHTTPServer:
    """Serve deterministic CMS question-detail responses on 127.0.0.1.

    Intake resolves question candidates through the CMS question-detail API
    (tests monkeypatch it instead); pointing CMS_BASE_URL at this stub keeps
    the smoke run offline without touching production code.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            uuid = parse_qs(urlparse(self.path).query).get("uuid", [""])[0]
            payload = {
                "code": 0,
                "message": "success",
                "data": {
                    "question_uuid": uuid,
                    "question_title": f"E2E 题目 {uuid}",
                    "body": {"content": f"E2E stub stem for {uuid}"},
                },
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("cms-stub: " + format, *args)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _backend_env(port: int, db_name: str, cms_base_url: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in _CMS_ENV_KEYS:
        env[key] = ""
    env.update(
        {
            "AGENT_LEGION_SKIP_DOTENV": "1",
            "AGENT_LEGION_DATABASE_URL": f"postgresql://127.0.0.1:5432/{db_name}",
            "AGENT_LEGION_DATA_DIR": str(DATA_DIR),
            "AGENT_LEGION_WORKER_REGISTER_TOKEN": "ci-only-dummy-register-token",
            "CMS_BASE_URL": cms_base_url,
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
    db_name = _e2e_database_name()
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
        _reset_database(db_name)
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        DATA_DIR.mkdir(parents=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        cms_stub = _start_cms_stub(cms_stub_port)
        backend_proc = _start_process(
            _backend_command(backend_port),
            cwd=PROJECT_ROOT,
            env=_backend_env(backend_port, db_name, cms_base_url),
            log_path=backend_log,
        )
        backend_base_url = f"http://127.0.0.1:{backend_port}"
        if not _wait_for_http(f"{backend_base_url}/api/health"):
            logger.error("Backend failed to start; log tail:\n%s", _log_tail(backend_log))
            return 1

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
