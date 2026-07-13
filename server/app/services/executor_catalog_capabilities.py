from typing import Any


def capability_detail(capability: str, config: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"name": capability}
    handler = getattr(config, "handler", None)
    skill = getattr(config, "skill", None)
    tools = getattr(config, "tools", ())
    if handler:
        detail["handler"] = handler
    if skill:
        detail["skill"] = skill
    if tools:
        detail["tools"] = list(tools)
    return detail
