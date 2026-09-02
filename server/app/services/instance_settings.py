"""Hydrate instance-level settings from the DB into the loaded Settings.

The DB document (``global_settings`` key ``instance``, managed via the admin
API) carries the instance-level tunables retired from yaml. Hydration runs
once at startup (``create_app``, right after ``JobQueries`` is constructed)
and takes effect on restart; there is no runtime hot-reload:

- executor runtime scalars plus ``workflows.enabled`` /
  ``agent_workers.{max_archive_bytes,min_protocol_version}`` and
  ``code_capacity`` are merged onto the loaded ``ExecutorRuntimeConfig`` and
  re-validated;
- ``cleanup`` / ``monitoring`` values are written back into ``settings.config``
  for construction-time consumers (OpsMetricsService, CleanupConfig, WorkflowMaintenance).
"""

from __future__ import annotations

from typing import Any

from server.app.configuration.executor_runtime import ExecutorRuntimeConfig
from server.app.configuration.instance_defaults import (
    DEFAULT_CLEANUP_CONFIG,
    DEFAULT_MONITORING_CONFIG,
)
from server.app.db.dialect import ConnectSource
from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.settings import Settings

# Top-level ExecutorRuntimeConfig scalars managed by the instance document.
_EXECUTOR_SCALAR_KEYS = (
    "heartbeat_interval_seconds",
    "lease_ttl_seconds",
    "heartbeat_failure_threshold",
    "sweeper_enabled",
    "sweeper_interval_seconds",
    "code_capacity",
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
        # Materials TTL (design §10): 0 = disabled; read fresh from the DB at
        # material completion/sweep time, never hydrated into Settings.
        "materials_ttl_days": 0,
        # Execution-plane row retention (#354): 0 = disabled (safe default);
        # read fresh from the DB at sweep time, never hydrated into Settings.
        "execution_retention_days": 0,
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


# Host-private state blocks riding the stored ``instance`` document: they
# are written by background machinery through InstanceSettingsStore.update
# and must never surface in the admin contract (InstanceSettingsDocument is
# extra=forbid, so a stray block would 500 the GET/PUT round-trip).
_PRIVATE_BLOCKS = ("openclaw", "execution_retention_cursor")


def _strip_retired_blocks(stored: dict[str, Any]) -> dict[str, Any]:
    """Remove non-contract top-level blocks from a stored document copy.

    - ``openclaw``: retired with the openclaw runtime (#75); stored documents
      from older deployments still carry it.
    - ``execution_retention_cursor`` (#354): the retention sweep's persisted
      keyset high-water marks. A concurrent admin PUT (whole-document
      replace) can wipe it — that only costs the sweep a cursor reset, the
      deletion predicate re-filters everything on the next pass.
    Both are stripped at read time (before response validation) so the
    effective document matches the current shape without a data migration.
    """
    if not any(block in stored for block in _PRIVATE_BLOCKS):
        return stored
    return {key: value for key, value in stored.items() if key not in _PRIVATE_BLOCKS}


def effective_instance_document(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Return the effective document: stored values over code defaults."""
    if stored is None:
        return default_instance_document()
    return _merge(default_instance_document(), _strip_retired_blocks(stored))


def apply_instance_settings(settings: Settings, database_dsn: ConnectSource) -> None:
    """Overlay the stored instance document onto ``settings``; no-op when unset.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187).
    """
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
