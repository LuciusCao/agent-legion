"""Code defaults for the retired ``config/app.yaml`` keys.

bootstrap/security-level keys are env-only with these code defaults (= the
last tracked yaml values); cleanup/monitoring are hydrated from the DB
instance settings document when one exists. The retired ``openclaw:`` yaml
section defaults live in ``openclaw_defaults.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.configuration.openclaw_defaults import apply_openclaw_config_defaults

if TYPE_CHECKING:
    from server.app.executors.runtime_config import AgentWorkersRuntimeConfig

DEFAULT_DATABASE_URL = "postgresql://127.0.0.1:5432/agent_legion"
DEFAULT_DATA_DIR = "data"
DEFAULT_CLEANUP_CONFIG: dict[str, Any] = {
    "log_retention_days": 7,
    "run_dir_retention_days": 3,
    "interval_seconds": 3600,
}
DEFAULT_MONITORING_CONFIG: dict[str, Any] = {"sample_interval_seconds": 60, "retention_days": 30}
# Repo-relative default for the worker register token file (was pinned by the
# retired workflow.yaml agent_workers section).
DEFAULT_WORKER_REGISTER_TOKEN_FILE = "deploy/secrets/agent_worker_register_token"


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


def resolve_worker_register_token(agent_workers: AgentWorkersRuntimeConfig, root_dir: Path) -> None:
    """Populate ``agent_workers.register_token`` from its file when unset.

    An explicit ``register_token_file`` must exist (fail fast); otherwise the
    repo-local default secrets path is read when present (previously pinned
    by the retired workflow.yaml agent_workers section).
    """
    token_file = agent_workers.register_token_file
    if token_file:
        if not agent_workers.register_token:
            agent_workers.register_token = Path(token_file).read_text(encoding="utf-8").strip()
        return
    default_file = root_dir / DEFAULT_WORKER_REGISTER_TOKEN_FILE
    if not agent_workers.register_token and default_file.is_file():
        agent_workers.register_token = default_file.read_text(encoding="utf-8").strip()
