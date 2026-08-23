"""Connectivity probe for the instance object store.

Two consumers share the same short-timeout ``head_bucket`` probe: the
startup self-check (one log line, never fatal — an unconfigured or
unreachable store is a deliberate degrade, not a boot failure) and the
``/api/health`` storage field (behind a few-second cache so health scrapes
never hammer RustFS). Reasons stay in server logs; the API only ever
exposes the configured/reachable booleans.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from server.app.storage.s3_client import S3StorageClient, build_s3_storage
from server.app.storage.s3_settings import S3Settings, load_s3_settings

PROBE_TIMEOUT_SECONDS = 2.0
HEALTH_CACHE_TTL_SECONDS = 5.0

logger = logging.getLogger(__name__)


def _head_bucket(settings: S3Settings, timeout_seconds: float) -> None:
    """head_bucket against the configured store with a hard short timeout."""
    import boto3
    from botocore.config import Config

    kwargs: dict[str, Any] = {
        "region_name": settings.region,
        "config": Config(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"max_attempts": 0},
        ),
    }
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.access_key:
        kwargs["aws_access_key_id"] = settings.access_key
        kwargs["aws_secret_access_key"] = settings.secret_key
    boto3.client("s3", **kwargs).head_bucket(Bucket=settings.bucket)


def probe_settings(
    settings: S3Settings, timeout_seconds: float = PROBE_TIMEOUT_SECONDS
) -> str | None:
    """Probe the store; None when reachable, else a short reason for logs."""
    try:
        _head_bucket(settings, timeout_seconds)
    except Exception as exc:  # a probe must never propagate
        return f"{type(exc).__name__}: {exc}"
    return None


def build_s3_storage_checked() -> S3StorageClient | None:
    """build_s3_storage plus the startup self-check log line (never fatal)."""
    settings = load_s3_settings()
    if settings is None:
        logger.info(
            "materials storage: configured=false (AGENT_LEGION_S3_BUCKET unset); "
            "materials API will degrade to 503"
        )
    else:
        reason = probe_settings(settings)
        if reason is None:
            logger.info("materials storage: OK")
        else:
            logger.warning("materials storage: DEGRADED: %s", reason)
    return build_s3_storage()


class StorageHealthCache:
    """Caches the probe verdict so /api/health never probes per request."""

    def __init__(self, ttl_seconds: float = HEALTH_CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._expires_at = 0.0
        self._cached: dict[str, bool] | None = None

    def status(self) -> dict[str, bool]:
        """Return {configured, reachable}; probes at most once per TTL."""
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now < self._expires_at:
                return self._cached
        settings = load_s3_settings()
        if settings is None:
            result = {"configured": False, "reachable": False}
        else:
            result = {"configured": True, "reachable": probe_settings(settings) is None}
        with self._lock:
            self._cached = result
            self._expires_at = now + self._ttl_seconds
        return result


def cached_storage_status(app_state: Any) -> dict[str, bool]:
    """Return the cached storage status, creating the cache on first use."""
    cache = getattr(app_state, "storage_health_cache", None)
    if cache is None:
        cache = StorageHealthCache()
        app_state.storage_health_cache = cache
    return cache.status()
