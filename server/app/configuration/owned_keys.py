from __future__ import annotations

CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "app.yaml": frozenset({"database", "data_dir", "server", "cleanup", "monitoring"}),
    "agent_legion.yaml": frozenset({"asr", "cms", "openclaw"}),
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


def owned_keys_for_file(name: str) -> frozenset[str]:
    """Return the owned top-level keys for a split config file name."""
    return CONFIG_FILE_KEYS[name]
