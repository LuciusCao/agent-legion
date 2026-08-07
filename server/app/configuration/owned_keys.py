from __future__ import annotations

from pathlib import Path

# config/app.yaml and config/workflow.yaml are retired: database.url/data_dir/
# server.cors are env-only (code defaults match the retired yaml values),
# cleanup/monitoring plus the executor runtime tuning keys live in the DB
# ``global_settings`` document ``instance`` (admin API), and executor
# definitions live in the DB ``versioned_entities`` table (seeded from the
# built-in factory catalog at startup, managed in Studio).
CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "agent_legion.yaml": frozenset({"asr", "openclaw"}),
}

# Retired split files: their presence means the deployment predates the
# configuration productization migrations, so fail fast with migration
# guidance instead of silently ignoring half the configuration.
RETIRED_FILE_NAMES = ("app.yaml", "workflow.yaml")

_RETIRED_FILE_GUIDANCE = {
    "app.yaml": (
        "config/app.yaml was retired (instance-level settings moved out of yaml). "
        "Migrate: database.url -> env AGENT_LEGION_DATABASE_URL; data_dir -> env "
        "AGENT_LEGION_DATA_DIR; server.cors -> env AGENT_LEGION_CORS_ALLOW_ORIGINS / "
        "AGENT_LEGION_CORS_ALLOW_CREDENTIALS; cleanup/monitoring and executor runtime "
        "tuning -> the DB instance settings document (/api/admin/instance-settings); "
        "then delete config/app.yaml."
    ),
    "workflow.yaml": (
        "config/workflow.yaml was retired. Migrate: executors definitions -> the DB "
        "versioned_entities table (built-in catalog seeded at startup, managed in "
        "Studio); the agents catalog retired earlier -> Studio Agents manager; "
        "then delete config/workflow.yaml."
    ),
}


def owned_keys_for_file(name: str) -> frozenset[str]:
    """Return the owned top-level keys for a split config file name."""
    return CONFIG_FILE_KEYS[name]


def retired_split_files(config_dir: Path) -> list[str]:
    """Return repo-relative names of retired split files present on disk."""
    return [f"config/{name}" for name in RETIRED_FILE_NAMES if (config_dir / name).is_file()]


def retired_file_guidance(names: list[str]) -> str:
    details = " ".join(_RETIRED_FILE_GUIDANCE[Path(name).name] for name in names)
    return f"retired configuration file(s) present: {', '.join(names)}. {details}"
