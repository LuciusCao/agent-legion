"""Standalone Agent Worker startup for the browser smoke main-flow spec.

The Host never executes Agent nodes locally (dispatch only enqueues into the
broker; claiming is the external Worker HTTP API), so the main-flow run
starts ``worker/executor.py`` as a subprocess — the real claim/execute/upload
protocol, no test double. Registration uses a workspace-scoped token issued
straight through the Host's register-token store (same code path as the
admin API, but without creating the admin user — smoke-auth.spec.ts owns the
fresh-database bootstrap flow), written to the conventional
``register_tokens/`` directory next to the generated worker.yaml.

The velites model registry path travels in the worker's ``environment``
block: it covers both registration-time model discovery (``velites models
list --json``) and execution-time provider resolution.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.e2e._llm_stub import StubGateway, write_models_json
from scripts.e2e._main_flow_seed import (
    AGENT_CAPABILITY,
    DRAFT_CONTENT,
    DRAFT_OUTPUT,
    seed_main_flow_workspace,
)
from server.app.agent_control.register_tokens import AgentRegisterTokenStore

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def ensure_velites_binary() -> Path:
    """Build/install the velites binary into data/bin (freshness-stamped).

    Both execution paths are fail-closed without it: Host-side code nodes
    need ``velites sandbox wrap`` on PATH (the runner prepends data/bin to
    the backend env), and the Worker resolves its own copy from data/bin.
    """
    binary = _PROJECT_ROOT / "data" / "bin" / "velites"
    result = subprocess.run(
        [str(_PROJECT_ROOT / "scripts" / "ensure-velites.sh"), "--dest", "data/bin"],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not binary.is_file():
        raise RuntimeError(
            "velites binary is required for the main-flow smoke (Host code sandbox "
            "+ Worker agent runtime) but ensure-velites.sh failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    logger.info("%s", (result.stdout or "velites binary ready").strip().splitlines()[-1])
    return binary


def issue_register_token(dsn: str, workspace_id: str) -> str:
    """Issue a workspace-scoped register token via the Host's token store."""
    _token_id, plaintext = AgentRegisterTokenStore(dsn).issue_register_token(
        workspace_id=workspace_id, label="e2e-main-flow"
    )
    return plaintext


def write_worker_config(
    config_dir: Path,
    *,
    host_url: str,
    work_root: Path,
    models_path: Path,
    register_token: str,
    capability: str,
) -> Path:
    """Write worker.yaml + the scoped token file; returns the yaml path."""
    config_dir.mkdir(parents=True, exist_ok=True)
    token_id = register_token.partition(".")[0]
    token_dir = config_dir / "register_tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / f"{token_id}.token").write_text(register_token, encoding="utf-8")
    # Registration credential at rest; mirror the control_token 0600 hygiene.
    (token_dir / f"{token_id}.token").chmod(0o600)
    config = {
        "host_url": host_url,
        "worker_id": "e2e-worker",
        "name": "E2E Worker",
        "runtimes": ["velites"],
        "capabilities": [capability],
        "max_concurrency": 2,
        # 0 = agent-only: code nodes stay on the Host's local code pool.
        "max_code_concurrency": 0,
        "claim_enabled": True,
        "work_root": str(work_root),
        "poll_interval_seconds": 1,
        "heartbeat_interval_seconds": 5,
        "shutdown_grace_seconds": 10,
        "environment": {"VELITES_MODELS_PATH": str(models_path)},
    }
    yaml_path = config_dir / "worker.yaml"
    yaml_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return yaml_path


def start_main_flow_runtime(
    *, dsn: str, data_dir: Path, backend_base_url: str, log_path: Path
) -> tuple[StubGateway, subprocess.Popen]:
    """Assemble the main-flow runtime: stub LLM + workspace seed + Worker.

    Returns the stub gateway (caller closes it) and the Worker subprocess
    (caller terminates it). Runs after the backend is healthy: the schema
    exists and the startup pause-reset has already happened.
    """
    stub = StubGateway(DRAFT_OUTPUT, DRAFT_CONTENT)
    try:
        models_path = write_models_json(data_dir / "velites-models.json", stub.base_url)
        workspace_id = seed_main_flow_workspace(dsn, data_dir)
        register_token = issue_register_token(dsn, workspace_id)
        worker_yaml = write_worker_config(
            data_dir / "e2e-worker",
            host_url=backend_base_url,
            work_root=data_dir / "e2e-worker" / "work",
            models_path=models_path,
            register_token=register_token,
            capability=AGENT_CAPABILITY,
        )
        env = dict(os.environ)
        env["PATH"] = f"{_PROJECT_ROOT / 'data' / 'bin'}:{env.get('PATH', '')}"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Starting: %s worker/executor.py --config %s", sys.executable, worker_yaml)
        with log_path.open("wb") as log:
            proc = subprocess.Popen(
                [sys.executable, "worker/executor.py", "--config", str(worker_yaml)],
                cwd=_PROJECT_ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
    except Exception:
        # Ownership only transfers to the caller on success; never leak the
        # stub's server thread on a seed/token/launch failure.
        stub.close()
        raise
    return stub, proc
