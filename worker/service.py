#!/usr/bin/env python3
"""Local HTTP control plane and status UI for an Agent Legion Worker."""

from __future__ import annotations

import argparse
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from worker.host_client import Client
from worker.registration_token import registration_token_configured
from worker.service_models import WorkerConfigPayload
from worker.supervisor import WorkerConfigStore, WorkerSupervisor, public_config

logger = logging.getLogger(__name__)


_HOT_CONFIG_FIELDS = {"claim_enabled", "max_concurrency"}


def _public_config_response(store: WorkerConfigStore, config: dict[str, Any]) -> dict[str, Any]:
    return {
        **public_config(config),
        "register_token_configured": registration_token_configured(config),
    }


def _revoke_previous_worker(config: dict[str, Any]) -> None:
    """Best-effort：改 worker_id 前在 Host 上吊销旧注册。

    失败只记 warning 不阻断保存——退化为旧行为（旧 worker_id 无心跳后离线残留）。"""
    host_url = str(config.get("host_url", ""))
    worker_id = str(config.get("worker_id", ""))
    if not host_url or not worker_id:
        return
    try:
        token = Path(str(config["register_token_file"])).read_text(encoding="utf-8").strip()
        Client(host_url).revoke(worker_id, token)
    except Exception as exc:  # noqa: BLE001 — best-effort，任何失败都不阻断保存
        logger.warning("吊销旧 Worker %s 失败，继续保存新配置：%s", worker_id, exc)


def create_app(supervisor: WorkerSupervisor, ui_dir: Path) -> FastAPI:
    token = supervisor.store.control_token()

    async def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        if not secrets.compare_digest(header, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="missing or invalid control token")

    guarded = [Depends(require_token)]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        supervisor.start()
        try:
            yield
        finally:
            supervisor.stop()

    app = FastAPI(title="Agent Legion Worker Service", version="1.0", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (ui_dir / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace('= "__WORKER_CONTROL_TOKEN__"', f'= "{token}"'))

    @app.get("/assets/{name}", include_in_schema=False)
    def asset(name: str) -> FileResponse:
        if name not in {"app.js", "styles.css"}:
            raise HTTPException(status_code=404, detail="asset not found")
        media_type = "text/javascript" if name.endswith(".js") else "text/css"
        return FileResponse(ui_dir / name, media_type=media_type)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status", dependencies=guarded)
    def status() -> dict[str, Any]:
        return supervisor.status()

    @app.get("/api/config", dependencies=guarded)
    def get_config() -> dict[str, Any]:
        config = supervisor.store.read(require_identity=False)
        return _public_config_response(supervisor.store, config)

    @app.put("/api/config", dependencies=guarded)
    def put_config(payload: WorkerConfigPayload) -> dict[str, Any]:
        try:
            fields = payload.model_dump(exclude_none=True)
            registration_token = fields.pop("register_token", None)
            previous = supervisor.store.read(require_identity=False)
            new_worker_id = fields.get("worker_id")
            if new_worker_id is not None and new_worker_id != previous["worker_id"]:
                _revoke_previous_worker(previous)
            config = supervisor.store.update_public(fields, registration_token=registration_token)
            changed = {field for field in fields if previous.get(field) != config.get(field)}
            restarted = bool(changed - _HOT_CONFIG_FIELDS or registration_token is not None)
            if restarted:
                supervisor.restart()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "config": _public_config_response(supervisor.store, config),
            "status": supervisor.status(),
            "restarted": restarted,
        }

    @app.post("/api/restart", dependencies=guarded)
    def restart() -> dict[str, Any]:
        supervisor.restart()
        return supervisor.status()

    @app.get("/api/logs", dependencies=guarded)
    def logs(limit: int = Query(default=200, ge=1, le=500)) -> dict[str, list[str]]:
        return {"lines": supervisor.logs(limit)}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Agent Legion Worker Service")
    parser.add_argument("--config", type=Path, default=Path("config/agent-worker.yaml"))
    parser.add_argument("--state-dir", type=Path, default=Path("data/agent-worker-service"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    worker_dir = Path(__file__).resolve().parent
    store = WorkerConfigStore(args.state_dir.resolve(), args.config.resolve())
    supervisor = WorkerSupervisor(store, worker_dir / "executor.py")
    app = create_app(supervisor, worker_dir / "ui")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
