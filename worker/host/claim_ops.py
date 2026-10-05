"""Claim operations for the Worker's Host client (split from
``worker.host.client`` for the file budget, #546 — mirrors the #352
``heartbeat_ops`` split).

The batch claim (issue #546) is the only claim call left: #547 retired the
Host's single-object response path, and with it the Worker-side single
method. The batch method's shape-sniff fallback stays — a pre-#546 Host
ignores the unknown request fields and answers a single claim object, and
wrapping it keeps a mixed fleet working through a Host upgrade.
"""

from __future__ import annotations

import json
from typing import Any

from worker.host.errors import HostResponseError, WorkerAuthError

_CLAIM_PATH = "/api/agent-executions/claim"


class ClaimOperations:
    """Mixin with the claim calls; the concrete client provides ``request``."""

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
        # #960：Host 侧的一切不合契约应答（意外状态码、不可解码或形状不对
        # 的 body）统一收口为 HostResponseError——executor claim 循环只对
        # 它与传输族（requests.RequestException）退避，Worker 侧编程错误
        # 不再被宽捕获伪装成「Host 不可用」。
        if status != 200:
            raise HostResponseError(f"Agent claim failed: HTTP {status}: {body[:300]!r}")
        try:
            document = json.loads(body)
        except ValueError as exc:
            raise HostResponseError(
                f"Agent claim failed: undecodable body: {body[:300]!r}"
            ) from exc
        if not isinstance(document, dict):
            raise HostResponseError(f"Agent claim failed: non-object body: {body[:300]!r}")
        claims = document.get("claims")
        if isinstance(claims, list):
            if not all(isinstance(item, dict) for item in claims):
                raise HostResponseError(f"Agent claim failed: non-object claim: {body[:300]!r}")
            return [dict(item) for item in claims]
        return [document]
