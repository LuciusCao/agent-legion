from __future__ import annotations

CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "app.yaml": frozenset(
        {"database", "data_dir", "server", "cleanup", "token_usage", "monitoring"}
    ),
    "agent_legion.yaml": frozenset({"asr", "cms", "resource_providers", "openclaw"}),
    "workflow.yaml": frozenset(
        {
            "executors",
            "agents",
            "agent_workers",
            "heartbeat_failure_threshold",
            "heartbeat_interval_seconds",
            "lease_ttl_seconds",
            "sweeper_enabled",
            "sweeper_interval_seconds",
            "workflows",
        }
    ),
}

# Legacy split-layout file names accepted during the rename transition window
# (config governance G4, issue 048). Maps the legacy name to its canonical
# replacement; the loader warns and still loads the legacy file when it is the
# only one present.
LEGACY_FILE_ALIASES: dict[str, str] = {"video_hive.yaml": "agent_legion.yaml"}


def owned_keys_for_file(name: str) -> frozenset[str]:
    """Return the owned top-level keys for a split config file name."""
    return CONFIG_FILE_KEYS[LEGACY_FILE_ALIASES.get(name, name)]
