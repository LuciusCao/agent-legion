from __future__ import annotations

CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "app.yaml": frozenset({"data_dir", "server", "worker", "cleanup"}),
    "video_hive.yaml": frozenset(
        {"asr", "cms", "resource_providers", "cleanup_video_after_assemble", "openclaw"}
    ),
    "workflow.yaml": frozenset(
        {
            "executors",
            "heartbeat_failure_threshold",
            "heartbeat_interval_seconds",
            "lease_ttl_seconds",
            "workflows",
        }
    ),
}
