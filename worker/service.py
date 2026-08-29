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

from worker.metrics_proxy import create_metrics_proxy_router
from worker.registration.token import registration_token_configured
from worker.service_bind import embed_control_token
from worker.service_models import WorkerConfigPayload
from worker.service_tokens import create_register_token_router
from worker.supervisor import WorkerConfigStore, WorkerSupervisor, public_config

logger = logging.getLogger(__name__)


# 三个容量参数（max_concurrency / max_code_concurrency /
# upload_max_concurrency）与 claim 开关全部热更：executor 主循环每轮
# 重读状态副本，调大立即放行新 claim、调小不杀在跑执行，新容量随下一次
# claim 上报 Host，无需重新注册或重启。code 容量 0→>0 的 velites 守卫由
# 循环内 hot_code_concurrency fail-closed 执行（缺失 velites 时拒绝热开
# 并打日志），不依赖重启预检。host_url / worker_id / runtimes 等进程级
# 配置仍走重启路径。
_HOT_CONFIG_FIELDS = {
    "claim_enabled",
    "max_concurrency",
    "max_code_concurrency",
    "upload_max_concurrency",
}


def _public_config_response(store: WorkerConfigStore, config: dict[str, Any]) -> dict[str, Any]:
    return {
        **public_config(config),
        "register_token_configured": registration_token_configured(config, store.state_dir),
    }


def _forget_previous_worker(config: dict[str, Any]) -> None:
    """改 worker_id 前的提示：Host 侧删除注册记录是 admin-only 操作。

    这里只记录一条日志，旧 worker_id 依赖 Host 的离线超时自然消失。"""
    worker_id = str(config.get("worker_id", ""))
    if worker_id:
        logger.info(
            "worker_id 已从 %s 变更；旧注册记录需管理员在 Host UI 删除（离线后自然不再领取）",
            worker_id,
        )


def create_app(supervisor: WorkerSupervisor, ui_dir: Path, *, embed_token: bool = True) -> FastAPI:
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

    # 本地控制面不需要浏览器长缓存：no-cache 强制每次校验，ETag 未变则 304。
    ui_assets = (
        "app.js",
        "styles.css",
        "icons.svg",
        "vendor/uPlot.iife.min.js",
        "vendor/uPlot.min.css",
    )
    media_types = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (ui_dir / "index.html").read_text(encoding="utf-8")
        if embed_token:
            html = html.replace('= "__WORKER_CONTROL_TOKEN__"', f'= "{token}"')
        return HTMLResponse(html, headers=no_cache)

    @app.get("/assets/{name:path}", include_in_schema=False)
    def asset(name: str) -> FileResponse:
        if name not in ui_assets:
            raise HTTPException(status_code=404, detail="asset not found")
        media_type = media_types[Path(name).suffix]
        return FileResponse(ui_dir / name, media_type=media_type, headers=no_cache)

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
                _forget_previous_worker(previous)
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

    app.include_router(create_metrics_proxy_router(supervisor), dependencies=guarded)
    app.include_router(create_register_token_router(supervisor, guarded))

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Agent Legion Worker Service")
    parser.add_argument("--config", type=Path, default=Path("config/agent-worker.yaml"))
    parser.add_argument("--state-dir", type=Path, default=Path("data/agent-worker-service"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    worker_dir = Path(__file__).resolve().parent  # worker/ 包根（executor.py 与 ui/ 同级）
    store = WorkerConfigStore(args.state_dir.resolve(), args.config.resolve())
    supervisor = WorkerSupervisor(store, worker_dir / "executor.py")
    app = create_app(supervisor, worker_dir / "ui", embed_token=embed_control_token(args.host))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
