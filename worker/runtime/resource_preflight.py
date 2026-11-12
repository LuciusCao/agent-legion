"""Concurrency-envelope preconditions for deployment-managed resource limits."""

from __future__ import annotations

import os

RESOURCE_LIMITS_MODE_ENV = "AGENT_WORKER_RESOURCE_LIMITS_MODE"
_REFERENCE_ENVELOPE = {
    "max_concurrency": 10,
    "max_code_concurrency": 0,
    "upload_max_concurrency": 4,
}


def resource_envelope_error(*, agent: int, code: int, upload: int) -> str | None:
    """Reject unreviewed default limits outside their measured concurrency envelope."""
    mode = os.environ.get(RESOURCE_LIMITS_MODE_ENV, "")
    if mode not in {"", "default", "explicit"}:
        return f"Agent Worker 资源限额预检失败：{RESOURCE_LIMITS_MODE_ENV} 仅支持 default/explicit"
    if mode != "default":
        return None
    values = {
        "max_concurrency": agent,
        "max_code_concurrency": code,
        "upload_max_concurrency": upload,
    }
    exceeded = [
        f"{key}={values[key]}>{limit}"
        for key, limit in _REFERENCE_ENVELOPE.items()
        if values[key] > limit
    ]
    if exceeded:
        return (
            "Agent Worker 资源限额预检失败：当前并发超出默认限额的测量 envelope（"
            + "、".join(exceeded)
            + "）。不得静默继承默认 limits；显式评审 AGENT_WORKER_MEM_LIMIT/"
            "AGENT_WORKER_PIDS_LIMIT（含显式 unlimited）并设 "
            f"{RESOURCE_LIMITS_MODE_ENV}=explicit 后重试"
        )
    return None
