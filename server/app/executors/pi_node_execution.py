from collections.abc import Mapping
from dataclasses import replace

from server.app.workflows.pi_config import PiConfig


def resolve_node_pi_config(
    default: PiConfig, runtime: Mapping[str, object]
) -> tuple[PiConfig, str]:
    raw = runtime.get("node_execution")
    if not isinstance(raw, Mapping):
        return default, ""

    def setting(name: str, fallback: str) -> str:
        value = raw.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else fallback

    return (
        replace(
            default,
            provider=setting("provider", default.provider),
            model=setting("model", default.model),
            thinking=setting("thinking", default.thinking),
        ),
        setting("prompt", ""),
    )
