"""Claim operations for the Worker's Host client (split from
``worker.host.client`` for the file budget, #546 — mirrors the #352
``heartbeat_ops`` split).

The single claim (legacy path) and the batch claim (issue #546) live together
here: both are claim-poll control calls. The batch method's shape-sniff
fallback (a pre-#546 Host ignores the unknown request fields and answers a
single claim object) is what the executor's mixed-fleet degeneration keys on.
"""

from __future__ import annotations

import json
from typing import Any

from worker.host.errors import WorkerAuthError

_CLAIM_PATH = "/api/agent-executions/claim"


class ClaimOperations:
    """Mixin with the claim calls; the concrete client provides ``request``."""

    def claim(
        self,
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"worker_id": worker_id}
        if max_concurrency is not None:
            payload["max_concurrency"] = max_concurrency
        if max_code_concurrency is not None:
            payload["max_code_concurrency"] = max_code_concurrency
        status, body = self.request(  # type: ignore[attr-defined]
            "POST",
            _CLAIM_PATH,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        if status == 204:
            return None
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"Agent claim failed: HTTP {status}: {body[:300]!r}")
        claim: dict[str, Any] | None = json.loads(body)
        return claim

    def claim_batch(
        self,
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
        *,
        limit: int,
        agent_limit: int,
        code_limit: int,
    ) -> list[dict[str, Any]]:
        """#546 batch claim: one round-trip asks for up to ``limit`` claims.

        Returns the claimed executions ([] = the empty-batch 204). Mixed-fleet
        fallback: a pre-#546 Host's pydantic model ignores the unknown batch
        fields and answers a single claim object — the shape sniff below wraps
        it into a one-element list, so the caller degenerates to the legacy
        per-claim loop with no version gate.
        """
        payload: dict[str, Any] = {
            "worker_id": worker_id,
            "limit": limit,
            "agent_limit": agent_limit,
            "code_limit": code_limit,
        }
        if max_concurrency is not None:
            payload["max_concurrency"] = max_concurrency
        if max_code_concurrency is not None:
            payload["max_code_concurrency"] = max_code_concurrency
        status, body = self.request(  # type: ignore[attr-defined]
            "POST",
            _CLAIM_PATH,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        if status == 204:
            return []
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"Agent claim failed: HTTP {status}: {body[:300]!r}")
        document: dict[str, Any] = json.loads(body)
        claims = document.get("claims")
        if isinstance(claims, list):
            return [dict(item) for item in claims]
        return [document]
