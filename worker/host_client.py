"""HTTP client for the Worker's pull protocol against the Host."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1

# Artifact upload retry: transient Host 5xx / connection failures get
# exponential backoff before the run is reported as failed.
_UPLOAD_MAX_ATTEMPTS = 3
_UPLOAD_BACKOFF_BASE_SECONDS = 1.0


class WorkerAuthError(RuntimeError):
    """Server rejected this Worker as unknown or revoked; re-registration is required."""


class Client:
    def __init__(self, host: str, token: str = "", timeout: float = 30) -> None:
        self.host = host.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
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

    def get_ops_metrics(
        self, granularity: str, hours: int, days: int, worker_id: str | None = None
    ) -> dict[str, Any]:
        """Fetch the Host ops-metrics overview (unauthenticated endpoint)."""
        params: dict[str, Any] = {"granularity": granularity, "hours": hours, "days": days}
        if worker_id:
            params["worker_id"] = worker_id
        query = urllib.parse.urlencode(params)
        status, body = self.request("GET", f"/api/metrics/overview?{query}")
        if status != 200:
            raise RuntimeError(f"ops metrics failed: HTTP {status}: {body[:300]!r}")
        return json.loads(body)

    def download(self, path: str, destination: Path) -> None:
        status, body = self.request("GET", path)
        if status != 200:
            raise RuntimeError(f"download failed: {path}: HTTP {status}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)

    def upload_artifact(self, path: Path) -> str:
        """Upload one output artifact, retrying transient Host failures.

        5xx responses and connection-level errors get exponential backoff
        (1s, 2s, 4s, …); 4xx and repeated failures raise immediately.
        """
        data = path.read_bytes()
        error = ""
        for attempt in range(_UPLOAD_MAX_ATTEMPTS):
            try:
                status, body = self.request("POST", "/api/artifacts", data=data)
            except urllib.error.URLError as exc:
                status, error = 0, str(exc)
            else:
                if status == 201:
                    return f"sha256:{json.loads(body)['hash']}"
                error = f"HTTP {status}: {body[:200]!r}"
            retryable = status == 0 or status >= 500
            if not retryable or attempt + 1 == _UPLOAD_MAX_ATTEMPTS:
                break
            time.sleep(_UPLOAD_BACKOFF_BASE_SECONDS * 2**attempt)
        raise RuntimeError(f"artifact upload failed: {error}")

    def heartbeat(self, execution_id: str, lease_id: str) -> int:
        status, _ = self.request(
            "POST",
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={"X-Agent-Lease-Id": lease_id},
        )
        return status

    def report(
        self, execution_id: str, lease_id: str, metadata: dict[str, Any], archive: Path
    ) -> None:
        status, body = self.request(
            "POST",
            f"/api/agent-executions/{execution_id}/result",
            data=archive.read_bytes(),
            headers={
                "X-Agent-Result": json.dumps(metadata, ensure_ascii=True),
                "X-Agent-Lease-Id": lease_id,
            },
        )
        if status != 204:
            raise RuntimeError(f"Agent result failed: HTTP {status}: {body[:300]!r}")
