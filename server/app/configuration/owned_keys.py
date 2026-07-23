from __future__ import annotations

CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "app.yaml": frozenset({"database", "data_dir", "server", "cleanup", "token_usage"}),
    "video_hive.yaml": frozenset(
        {"asr", "cms", "resource_providers", "cleanup_video_after_assemble", "openclaw"}
    ),
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
