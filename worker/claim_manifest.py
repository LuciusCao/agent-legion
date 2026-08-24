"""Apply Host-resolved live execution settings to an extracted bundle."""

from __future__ import annotations

from typing import Any


def apply_live_manifest(bundled: dict[str, Any], claimed: dict[str, Any]) -> dict[str, Any]:
    live = claimed.get("manifest")
    if not isinstance(live, dict):
        return bundled
    # artifact_uploads / input_artifacts：#160 D12 起 Host 在 claim 时内存态
    # 注入对象存储通道（enqueue 持久化的 bundle 里只有 CAS 形态）；旧 Host
    # 不带这些键时保留 bundled 原值（旧 CAS 通道）。
    for key in (
        "execution",
        "additional_prompt",
        "command_spec",
        "input_artifacts",
        "artifact_uploads",
    ):
        if key in live:
            bundled[key] = live[key]
    return bundled
