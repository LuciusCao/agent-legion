from __future__ import annotations

from pathlib import Path

# config/app.yaml is retired: database.url/data_dir/server.cors are env-only
# (code defaults match the retired yaml values), and cleanup/monitoring plus
# the executor runtime tuning keys live in the DB ``global_settings`` document
# ``instance`` (admin API). ``agents`` stays owned by workflow.yaml only so
# the retired-agent-catalog fail-fast in settings.py keeps firing.
CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "agent_legion.yaml": frozenset({"asr", "openclaw"}),
    "workflow.yaml": frozenset({"executors", "agents"}),
}

# Retired split files: their presence means the deployment predates the
# instance-settings migration, so fail fast with migration guidance instead of
# silently ignoring half the configuration.
RETIRED_FILE_NAMES = ("app.yaml",)

_RETIRED_FILE_GUIDANCE = (
    "retired configuration file(s) present: {names}. config/app.yaml was retired "
    "(instance-level settings moved out of yaml). Migrate: database.url -> env "
    "AGENT_LEGION_DATABASE_URL; data_dir -> env AGENT_LEGION_DATA_DIR; server.cors "
    "-> env AGENT_LEGION_CORS_ALLOW_ORIGINS / AGENT_LEGION_CORS_ALLOW_CREDENTIALS; "
    "cleanup/monitoring and executor runtime tuning (lease/heartbeat/sweeper/"
    "agent_workers limits) -> the DB instance settings document (admin API "
    "/api/admin/instance-settings); then delete config/app.yaml."
)


def owned_keys_for_file(name: str) -> frozenset[str]:
    """Return the owned top-level keys for a split config file name."""
    return CONFIG_FILE_KEYS[name]


def retired_split_files(config_dir: Path) -> list[str]:
    """Return repo-relative names of retired split files present on disk."""
    return [f"config/{name}" for name in RETIRED_FILE_NAMES if (config_dir / name).is_file()]


def retired_file_guidance(names: list[str]) -> str:
    return _RETIRED_FILE_GUIDANCE.format(names=", ".join(names))
