"""HTTP client for the Worker's pull protocol against the Host.

Control calls (register/claim/heartbeat/metrics) live here; retried bulk
transfers (download/upload/report/release-slot) come from the
``TransferOperations`` mixin in ``worker.host_transfer``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from worker.host_transfer import DEFAULT_TRANSFER_TIMEOUT, TransferOperations

PROTOCOL_VERSION = 1

DEFAULT_TIMEOUT = 30


class WorkerAuthError(RuntimeError):
    """Server rejected this Worker as unknown or revoked; re-registration is required."""


class Client(TransferOperations):
    def __init__(
        self,
        host: str,
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        transfer_timeout: float = DEFAULT_TRANSFER_TIMEOUT,
    ) -> None:
        self.host = host.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transfer_timeout = transfer_timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            f"{self.host}{path}",
            method=method,
            data=data,
            headers={
                **({"X-Agent-Worker-Token": self.token} if self.token else {}),
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout if timeout is None else timeout
            ) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def register(self, config: dict[str, Any], management_token: str) -> str:
        payload = {
            "worker_id": config["worker_id"],
            "name": config.get("name", config["worker_id"]),
            "runtimes": config["runtimes"],
            "capabilities": config.get("capabilities", []),
            "models": config.get("models", []),
            "max_concurrency": config["max_concurrency"],
            "labels": config.get("labels", {}),
            "protocol_version": PROTOCOL_VERSION,
        }
        status, body = self.request(
            "POST",
            "/api/agent-workers/register",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Worker-Register-Token": management_token,
            },
        )
        if status in (400, 401, 403, 409, 422):
            raise WorkerAuthError(
                f"Agent Worker registration rejected: HTTP {status}: {body[:300]!r}"
            )
        if status != 201:
            raise RuntimeError(f"Agent Worker registration failed: HTTP {status}: {body[:300]!r}")
        self.token = str(json.loads(body)["worker_token"])
        return self.token

    def revoke(self, worker_id: str, management_token: str) -> None:
        """Revoke a Worker registration on the Host (same credential as register)."""
        status, body = self.request(
            "POST",
            f"/api/agent-workers/{worker_id}/revoke",
            headers={"X-Agent-Worker-Register-Token": management_token},
        )
        if status != 200:
            raise RuntimeError(f"Agent Worker revoke failed: HTTP {status}: {body[:300]!r}")

    def get_self(self) -> dict[str, Any]:
        """Return this Worker's Host-side record using its per-worker token."""
        status, body = self.request("GET", "/api/agent-workers/self")
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"Agent Worker status failed: HTTP {status}: {body[:300]!r}")
        return dict(json.loads(body))

    def claim(self, worker_id: str, max_concurrency: int | None = None) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"worker_id": worker_id}
        if max_concurrency is not None:
            payload["max_concurrency"] = max_concurrency
        status, body = self.request(
            "POST",
            "/api/agent-executions/claim",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        if status == 204:
            return None
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"Agent claim failed: HTTP {status}: {body[:300]!r}")
        return json.loads(body)

    def get_ops_metrics(self, granularity: str) -> dict[str, Any]:
        """Fetch this Worker's metrics with its issued Worker token."""
        query = urllib.parse.urlencode({"granularity": granularity})
        status, body = self.request("GET", f"/api/agent-workers/self/metrics?{query}")
        if status in (401, 409):
            raise WorkerAuthError(f"HTTP {status}: {body[:300]!r}")
        if status != 200:
            raise RuntimeError(f"ops metrics failed: HTTP {status}: {body[:300]!r}")
        return json.loads(body)

    def heartbeat(self, execution_id: str, lease_id: str) -> int:
        status, _ = self.request(
            "POST",
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={"X-Agent-Lease-Id": lease_id},
        )
        return status
