"""Volatile Worker-token-authenticated metrics cache shared with the local UI."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from worker.status import ENV_VAR

METRICS_FILENAME = "ops_metrics.json"
METRIC_WINDOWS = ("6h", "24h", "30d")
_REFRESH_SECONDS = 60.0


class MetricsClient(Protocol):
    def get_ops_metrics(self, granularity: str) -> dict[str, Any]: ...


def metrics_cache_key(granularity: str) -> str:
    # 窗口由 granularity 唯一决定（6h/24h/30d），不再带 hours/days 参数。
    return granularity


def metrics_cache_path(state_dir: Path) -> Path:
    return state_dir / METRICS_FILENAME


def read_metrics_cache(path: Path) -> dict[str, Any]:
    """Return only a live writer's cache; stale or malformed files are ignored."""
    empty: dict[str, Any] = {"snapshots": {}, "error": None, "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        os.kill(int(payload["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return empty
    snapshots = payload.get("snapshots")
    return {
        "snapshots": snapshots if isinstance(snapshots, dict) else {},
        "error": payload.get("error") if isinstance(payload.get("error"), str) else None,
        "updated_at": payload.get("updated_at"),
    }


class WorkerMetricsCache:
    """Refresh fixed UI windows without ever persisting the Worker credential."""

    def __init__(self, path: Path | None, refresh_seconds: float = _REFRESH_SECONDS) -> None:
        self._path = path
        self._refresh_seconds = refresh_seconds
        self._next_refresh = 0.0
        self._snapshots: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls) -> WorkerMetricsCache:
        raw = os.environ.get(ENV_VAR, "").strip()
        path = Path(raw).with_name(METRICS_FILENAME) if raw else None
        return cls(path)

    def refresh(self, client: MetricsClient, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if current < self._next_refresh:
            return
        errors: list[str] = []
        for granularity in METRIC_WINDOWS:
            try:
                payload = client.get_ops_metrics(granularity)
            except Exception as exc:  # noqa: BLE001 - one failed window must not block Worker status
                errors.append(f"{granularity}: {exc}")
            else:
                self._snapshots[metrics_cache_key(granularity)] = payload
        self._next_refresh = current + self._refresh_seconds
        self.publish(self._snapshots, "; ".join(errors) or None)

    def publish(
        self,
        snapshots: dict[str, dict[str, Any]],
        error: str | None = None,
    ) -> None:
        self._snapshots = dict(snapshots)
        if self._path is None:
            return
        payload = {
            "pid": os.getpid(),
            "snapshots": self._snapshots,
            "error": error,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            descriptor, temporary = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=f"{self._path.stem}.",
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._path)
            except BaseException:
                with suppress(OSError):
                    os.unlink(temporary)
                raise
        except OSError as exc:
            print(f"metrics cache write failed: {exc}", flush=True)
