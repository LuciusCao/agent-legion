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
        node_concurrency_limits: dict[str, int] | None = None,
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
        # #1158 节点级并发上限：与容量同渠道每次 claim 重声明（Host 热同步），
        # 显式空 map 也携带——它是「清空库存值」的唯一通道；None 不携带（旧
        # 行为，Host 保留库存值）。
        if node_concurrency_limits is not None:
            payload["node_concurrency_limits"] = node_concurrency_limits
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
        except (ValueError, RecursionError) as exc:
            raise HostResponseError(
                f"Agent claim failed: undecodable body: {body[:300]!r}"
            ) from exc
        claims = document.get("claims") if isinstance(document, dict) else None
        # pre-#546 Host 的单对象兜底只认带 execution_id 的对象：中间盒的
        # 200 JSON（如 {"error": ...}）、{"claims": null} 等不得冒充 claim。
        if claims is None and isinstance(document, dict) and "execution_id" in document:
            claims = [document]
        # 每项须是含 execution_id / node_key 的对象（submit 路径的
        # events.execution_base 硬读这两个键），否则在 executor 内 KeyError。
        if not isinstance(claims, list) or not all(_is_claim(item) for item in claims):
            raise HostResponseError(f"Agent claim failed: off-contract body: {body[:300]!r}")
        return [dict(item) for item in claims]


def _is_claim(item: Any) -> bool:
    return isinstance(item, dict) and "execution_id" in item and "node_key" in item
