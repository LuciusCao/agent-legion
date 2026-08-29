"""Scoped register-token routes for the Worker local control plane.

Split from ``worker/service.py`` (#250 budget floors, metrics_proxy 的 router
工厂先例)：register-token 的增删查是独立资源面，与配置/重启编排无共享
状态。行为零变化——路由集合、依赖注入与响应形状照旧，仅位置迁移。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from worker.service_models import RegisterTokenPayload
from worker.supervisor import WorkerSupervisor


def create_register_token_router(supervisor: WorkerSupervisor, guarded: list[Any]) -> APIRouter:
    """Register-token CRUD routes; ``guarded`` carries the caller's auth dependency."""
    router = APIRouter()

    @router.get("/api/register-tokens", dependencies=guarded)
    def list_register_tokens() -> dict[str, Any]:
        """Scoped token 清单（仅 id 与验证状态，永不回显明文）。"""
        tokens = supervisor.store.read_registration_tokens()
        token_status = supervisor.token_status()
        return {
            "tokens": [
                {
                    "token_id": row["token_id"],
                    "state": token_status.get(row["token_id"], "pending"),
                }
                for row in tokens
            ]
        }

    @router.post("/api/register-tokens", dependencies=guarded)
    def add_register_token(payload: RegisterTokenPayload) -> dict[str, Any]:
        """添加一个 scoped token 并重启（重注册会验证全部 token）。"""
        try:
            supervisor.store.upsert_registration_token(payload.register_token)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        supervisor.restart()
        return supervisor.status()

    @router.delete("/api/register-tokens/{token_id}", status_code=200, dependencies=guarded)
    def remove_register_token(token_id: str) -> dict[str, Any]:
        if not supervisor.store.remove_registration_token(token_id):
            raise HTTPException(status_code=404, detail="token not found")
        supervisor.restart()
        return supervisor.status()

    return router
