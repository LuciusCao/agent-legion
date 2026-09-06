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
            # Degraded to single beats (PR #497 review): one INFO line per
            # transition makes the rolling-upgrade window's cost visible —
            # N leases × SINGLE_BEAT_TIMEOUT_SECONDS is the per-tick ceiling
            # the coordinator now rides on short-lived per-lease threads.
            logger.info(
                "batch heartbeat endpoint unavailable (HTTP %s); degraded to single"
                " beats for %d leases (per-tick ceiling %d × %.0fs)",
                status,
                len(executions),
                len(executions),
                SINGLE_BEAT_TIMEOUT_SECONDS,
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
