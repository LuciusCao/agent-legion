"""Hydrate instance-level settings from the DB into the loaded Settings.

The DB document (``global_settings`` key ``instance``, managed via the admin
API) carries the instance-level tunables retired from yaml. Hydration runs
once at startup (``create_app``, right after ``JobQueries`` is constructed)
and takes effect on restart; there is no runtime hot-reload:

- executor runtime scalars plus ``workflows.enabled`` /
  ``agent_workers.{max_archive_bytes,min_protocol_version}`` are merged onto
  the already-loaded ``ExecutorRuntimeConfig`` (sub-blocks the DB does not
  manage, e.g. openclaw/agent_stock/agent_enqueue, keep their loaded values)
  and re-validated;
- ``cleanup`` / ``monitoring`` values are written back into
  ``settings.config`` for construction-time consumers (OpsMetricsService,
  CleanupConfig, WorkflowMaintenance).
"""

from __future__ import annotations

from typing import Any

from server.app.configuration.instance_defaults import (
    DEFAULT_CLEANUP_CONFIG,
    DEFAULT_MONITORING_CONFIG,
)
from server.app.db.connection import DatabaseDsn
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.settings import Settings

# Top-level ExecutorRuntimeConfig scalars managed by the instance document.
_EXECUTOR_SCALAR_KEYS = (
    "heartbeat_interval_seconds",
    "lease_ttl_seconds",
    "heartbeat_failure_threshold",
    "sweeper_enabled",
    "sweeper_interval_seconds",
)


def default_instance_document() -> dict[str, Any]:
    """Return the code-default instance settings document."""
    runtime = ExecutorRuntimeConfig()
    document: dict[str, Any] = {
        "cleanup": dict(DEFAULT_CLEANUP_CONFIG),
        "monitoring": dict(DEFAULT_MONITORING_CONFIG),
        "workflows": {"enabled": runtime.workflows.enabled},
        "agent_workers": {
            "max_archive_bytes": runtime.agent_workers.max_archive_bytes,
            "min_protocol_version": runtime.agent_workers.min_protocol_version,
        },
    }
    for key in _EXECUTOR_SCALAR_KEYS:
        document[key] = getattr(runtime, key)
    return document


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base`` without mutating either."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def effective_instance_document(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Return the effective document: stored values over code defaults."""
    if stored is None:
        return default_instance_document()
    return _merge(default_instance_document(), stored)


def apply_instance_settings(settings: Settings, database_dsn: DatabaseDsn) -> None:
    """Overlay the stored instance document onto ``settings``; no-op when unset."""
    stored = InstanceSettingsStore(database_dsn).get()
    if stored is None:
        return
    effective = effective_instance_document(stored)
    base = settings.executor_runtime.model_dump()
    for key in _EXECUTOR_SCALAR_KEYS:
        base[key] = effective[key]
    base["workflows"]["enabled"] = effective["workflows"]["enabled"]
    base["agent_workers"]["max_archive_bytes"] = effective["agent_workers"]["max_archive_bytes"]
    base["agent_workers"]["min_protocol_version"] = effective["agent_workers"][
        "min_protocol_version"
    ]
    settings.executor_runtime = ExecutorRuntimeConfig.model_validate(base)
    settings.config["cleanup"] = effective["cleanup"]
    settings.config["monitoring"] = effective["monitoring"]
