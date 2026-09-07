"""Heartbeat operations for the Worker's Host client (split from
``worker.host.client`` for the file budget, #352).

The single-execution beat (protocol v2 body) and the per-Worker batch beat
(protocol v5) live together here: both are lease-renewal control calls. The
batch method's 404/405 contract (None = the Host predates the endpoint) is
what the coordinator's mixed-fleet fallback keys on.
"""

from __future__ import annotations

import contextlib
import json
import logging

logger = logging.getLogger(__name__)

_BULK_PATH = "/api/agent-executions/heartbeats"

# Degraded per-execution beats (pre-v5 Host) run in one short-lived thread
# per lease; each call gets this cap instead of the client default so a slow
# Host response bounds only its own lease's beat (the thread parks, then the
# transport-level error it eventually raises is handled by the loop's
# per-beat error family) instead of blocking the coordinator or any other
# lease's renewal.
SINGLE_BEAT_TIMEOUT_SECONDS = 5.0


class HeartbeatOperations:
    """Mixin with the heartbeat calls; the concrete client provides ``request``."""

    def heartbeat(
        self,
        execution_id: str,
        lease_id: str,
        timeout: float | None = None,
    ) -> tuple[int, list[str]]:
        """Beat once; returns (status, cancelled_execution_ids).

        Protocol v2 Hosts answer 200 with a body listing this Worker's
        cancelled kind='code' executions; v1 answers 204 (no body).
        ``timeout`` overrides the client default for callers that beat in a
        shared thread (the degraded coordinator path)."""
        status, body = self.request(  # type: ignore[attr-defined]
            "POST",
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={"X-Agent-Lease-Id": lease_id},
            timeout=timeout,
        )
        cancelled: list[str] = []
        if status == 200:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                cancelled = [
                    str(value) for value in json.loads(body).get("cancelled_execution_ids", [])
                ]
        return status, cancelled

    def heartbeat_batch(
        self, executions: list[tuple[str, str]]
    ) -> tuple[int, dict[str, list[str]]] | None:
        """One batch beat for every live lease (protocol v5, #352).

        Returns (200, body) on success, ``None`` when the Host predates the
        batch endpoint (404/405) — the caller falls back to per-execution
        beats. Any other status raises (transport errors already raise inside
        ``request``), matching the single-beat error family."""
        payload = {
            "executions": [
                {"execution_id": execution_id, "lease_id": lease_id}
                for execution_id, lease_id in executions
            ]
        }
        status, body = self.request(  # type: ignore[attr-defined]
            "POST",
            _BULK_PATH,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        if status in (404, 405):
            # PR #497 review：降级转场打一条 INFO——滚动升级窗口的每拍上限
            # （N leases × 5s，per-lease 线程化后为单线程停泊上限）可见化。
            # f-string 内插而非 %s 参数表：ruff format 的 magic-trailing-comma
            # 会把多参数调用 explode 成每参数一行，预算装不下（exemption 83）。
            n = len(executions)
            logger.info(
                f"batch heartbeat endpoint unavailable (HTTP {status}); degraded to"
                f" single beats for {n} leases"
                f" (per-tick ceiling {n} × {SINGLE_BEAT_TIMEOUT_SECONDS:.0f}s)"
            )
            return None
        if status != 200:
            raise RuntimeError(f"batch heartbeat failed: HTTP {status}: {body[:300]!r}")
        document: dict[str, list[str]] = {}
        try:
            parsed = json.loads(body)
            document = {
                "renewed": [str(value) for value in parsed.get("renewed", [])],
                "lost": [str(value) for value in parsed.get("lost", [])],
                "cancelled_execution_ids": [
                    str(value) for value in parsed.get("cancelled_execution_ids", [])
                ],
            }
        except (ValueError, TypeError, AttributeError):
            # #501（PR #497 review）：200 + 畸形 body 当「本拍无信息」处理
            # （保守——重试可能把批量心跳整体抖死），但必须留痕：lost 被静默
            # 丢弃曾是零日志黑洞，恢复的兜底是租约过期后的 Host 重调度。
            print(f"batch heartbeat 200 with unparseable body: {body[:300]!r}", flush=True)
        return status, document
