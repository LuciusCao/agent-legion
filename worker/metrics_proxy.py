"""Local Worker UI metrics endpoint backed by the child process's volatile cache."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from worker.metrics_cache import metrics_cache_key, metrics_cache_path, read_metrics_cache
from worker.supervisor import WorkerSupervisor


def create_metrics_proxy_router(supervisor: WorkerSupervisor) -> APIRouter:
    router = APIRouter()

    @router.get("/api/metrics/overview")
    def metrics_overview(
        granularity: Literal["6h", "24h", "30d"] = "6h",
    ) -> dict[str, Any]:
        cache = read_metrics_cache(metrics_cache_path(supervisor.store.state_dir))
        payload = cache["snapshots"].get(metrics_cache_key(granularity))
        if isinstance(payload, dict):
            return payload
        detail = cache["error"] or "等待 Worker 使用签发 token 同步 Host 监控数据"
        raise HTTPException(status_code=503, detail=f"Host 监控数据不可用：{detail}")

    return router
