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

_BULK_PATH = "/api/agent-executions/heartbeats"

# Degraded per-execution beats (pre-v5 Host) run in the single coordinator
# thread; each call gets this cap instead of the client default so one slow
# response cannot serially starve every other lease's renewal — the worst
# case per tick becomes leases × cap, and the transport-level error it
# eventually raises is handled by the loop's per-beat error family.
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
            return None
        if status != 200:
            raise RuntimeError(f"batch heartbeat failed: HTTP {status}: {body[:300]!r}")
        document: dict[str, list[str]] = {}
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            parsed = json.loads(body)
            document = {
                "renewed": [str(value) for value in parsed.get("renewed", [])],
                "lost": [str(value) for value in parsed.get("lost", [])],
                "cancelled_execution_ids": [
                    str(value) for value in parsed.get("cancelled_execution_ids", [])
                ],
            }
        return status, document
