"""Code defaults for the retired ``config/app.yaml`` keys.

bootstrap/security-level keys are env-only with these code defaults (= the
last tracked yaml values); cleanup/monitoring are hydrated from the DB
instance settings document when one exists. The retired ``openclaw:`` yaml
section defaults live in ``openclaw_defaults.py``.
"""

from __future__ import annotations

from typing import Any

from server.app.configuration.openclaw_defaults import apply_openclaw_config_defaults

DEFAULT_DATABASE_URL = "postgresql://127.0.0.1:5432/agent_legion"
DEFAULT_DATA_DIR = "data"
DEFAULT_CLEANUP_CONFIG: dict[str, Any] = {
    "log_retention_days": 7,
    "run_dir_retention_days": 3,
    "interval_seconds": 3600,
}
DEFAULT_MONITORING_CONFIG: dict[str, Any] = {"sample_interval_seconds": 60, "retention_days": 30}


def apply_instance_config_defaults(config: dict[str, Any]) -> None:
    """Fill code defaults for the retired app.yaml keys into the config dict.

    Consumers (CleanupConfig, OpsMetricsService, WorkflowMaintenance) read
    these sections from the config dict; explicit single-file configs may
    still carry them, so only missing keys are filled. ``server.cors``
    defaults stay in ``CorsSettings`` and are not written here. The retired
    ``openclaw:`` section is filled the same way (see openclaw_defaults.py).
    """
    config.setdefault("data_dir", DEFAULT_DATA_DIR)
    sections = (
        ("database", {"url": DEFAULT_DATABASE_URL}),
        ("cleanup", DEFAULT_CLEANUP_CONFIG),
        ("monitoring", DEFAULT_MONITORING_CONFIG),
    )
    for section, defaults in sections:
        node = config.get(section)
        if not isinstance(node, dict):
            config[section] = node = {}
        for key, value in defaults.items():
            node.setdefault(key, value)
    apply_openclaw_config_defaults(config)
