"""Heartbeat operations for the Worker's Host client (protocol v2 parse).

Split from ``worker.host.client`` for the file-size budget (#490): the
heartbeat's cancelled-list parsing is the protocol-v2 body contract, a
sibling of the bulk-transfer lane in ``transfer.py`` — both are mixins the
``Client`` composes.
"""

from __future__ import annotations

import contextlib
import json


class HeartbeatOperations:
    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        """Beat once; returns (status, cancelled_execution_ids).

        Protocol v2 Hosts answer 200 with a body listing this Worker's
        cancelled kind='code' executions; v1 answers 204 (no body)."""
        status, body = self.request(  # type: ignore[attr-defined]
            "POST",
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={"X-Agent-Lease-Id": lease_id},
        )
        cancelled: list[str] = []
        if status == 200:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                cancelled = [
                    str(value) for value in json.loads(body).get("cancelled_execution_ids", [])
                ]
        return status, cancelled
