from __future__ import annotations

from pathlib import Path

# All runtime split config files are retired: config/app.yaml and
# config/workflow.yaml first (database.url/data_dir/server.cors are env-only
# with code defaults, cleanup/monitoring plus the executor runtime tuning keys
# live in the DB ``global_settings`` document ``instance``, executor
# definitions live in the DB ``versioned_entities`` table), then
# config/agent_legion.yaml (the openclaw runtime section retired into the same
# DB instance settings document; the asr section retired with the legacy
# business transcription pipeline — business parameters and machine paths now
# live in the corresponding node configuration). No split file owns any
# top-level key anymore; the canonical layout loads zero files and starts
# from the code defaults plus env overrides.
CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {}

# Retired split files: their presence means the deployment predates the
# configuration productization migrations, so fail fast with migration
# guidance instead of silently ignoring half the configuration.
RETIRED_FILE_NAMES = ("app.yaml", "workflow.yaml", "agent_legion.yaml")

_RETIRED_FILE_GUIDANCE = {
    "app.yaml": "config/app.yaml was retired (instance-level settings moved out of yaml). Migrate: database.url -> env AGENT_LEGION_DATABASE_URL; data_dir -> env AGENT_LEGION_DATA_DIR; server.cors -> env AGENT_LEGION_CORS_ALLOW_ORIGINS / AGENT_LEGION_CORS_ALLOW_CREDENTIALS; cleanup/monitoring and executor runtime tuning -> the DB instance settings document (/api/admin/instance-settings); then delete config/app.yaml.",  # fmt: skip
    "workflow.yaml": "config/workflow.yaml was retired. Migrate: executors definitions -> the DB versioned_entities table (built-in catalog seeded at startup, managed in Studio); the agents catalog retired earlier -> Studio Agents manager; then delete config/workflow.yaml.",  # fmt: skip
    "agent_legion.yaml": "config/agent_legion.yaml was retired. Migrate: asr.provider / asr.timeout_seconds and ASR machine paths -> the corresponding node configuration in Studio (the ASR env channel retired with the legacy transcription pipeline); openclaw -> the DB instance settings document (/api/admin/instance-settings); then delete config/agent_legion.yaml.",  # fmt: skip
}


def owned_keys_for_file(name: str) -> frozenset[str]:
    """Owned top-level keys for a split file name; unknown names own nothing."""
    return CONFIG_FILE_KEYS.get(name, frozenset())


def retired_split_files(config_dir: Path) -> list[str]:
    """Return repo-relative names of retired split files present on disk."""
    return [f"config/{name}" for name in RETIRED_FILE_NAMES if (config_dir / name).is_file()]


def retired_file_guidance(names: list[str]) -> str:
    details = " ".join(_RETIRED_FILE_GUIDANCE[Path(name).name] for name in names)
    return f"retired configuration file(s) present: {', '.join(names)}. {details}"
