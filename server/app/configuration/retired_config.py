"""Retired configuration surfaces: fail fast with migration guidance.

Every check here guards dead config whose silent acceptance would be a hazard
-- an operator could believe a rotated credential is still active, or a
catalog that no longer loads would keep shaping deployments. Split out of
``settings.py`` (issue 287) so the rejection policy grows beside
``owned_keys.py`` (retired split files) instead of inside the settings
assembler; all checks run after the yaml load and before env overrides so
env-injected in-memory values are never mistaken for yaml keys.
"""

from __future__ import annotations

import os
from typing import Any


def reject_retired_config(config: dict[str, Any]) -> None:
    """Reject every retired yaml section/key and env var at load time.

    The order is load_settings' original sequence (cms, then the Agent
    catalog keys, then the worker register token); the first hit raises
    with migration guidance.
    """
    _reject_retired_cms_yaml_keys(config)
    _reject_retired_agent_yaml_keys(config)
    _reject_retired_register_token_config(config)


def _reject_retired_register_token_config(config: dict[str, Any]) -> None:
    """Fail fast on any leftover global register token configuration.

    The global token was retired with issue #35 (registration is scoped-token
    only, issued per workspace from the admin UI). Both the yaml
    ``agent_workers.register_token(_file)`` keys and the
    AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE) env vars are dead config that
    must not be silently ignored: an operator could otherwise believe a
    rotated token is active while every registration actually uses scoped
    tokens. Remove the keys; workers register with scoped tokens issued in the
    workspace settings (设置 → Agent 与 Worker)."""
    retired_keys = sorted(
        key
        for key in ("register_token", "register_token_file")
        if key in (config.get("agent_workers") or {})
    )
    if retired_keys:
        raise ValueError(
            "Unsupported agent_workers keys: "
            + ", ".join(f"agent_workers.{key}" for key in retired_keys)
            + ". The global worker register token was retired (issue #35); "
            "registration uses scoped tokens issued in the workspace settings "
            "(设置 → Agent 与 Worker). Remove these keys."
        )
    for env_var in (
        "AGENT_LEGION_WORKER_REGISTER_TOKEN",
        "AGENT_LEGION_WORKER_REGISTER_TOKEN_FILE",
    ):
        if os.environ.get(env_var):
            raise ValueError(
                f"Unsupported environment variable: {env_var}. The global worker "
                "register token was retired (issue #35); registration uses scoped "
                "tokens issued in the workspace settings (设置 → Agent 与 Worker). "
                "Unset it."
            )


def _reject_retired_cms_yaml_keys(config: dict[str, Any]) -> None:
    """Fail fast when the yaml still carries the retired ``cms:`` section.

    Config governance G2 (breaking): the CMS integration moved to
    instance-level external connections (admin settings → 外部服务连接);
    neither yaml nor env ``CMS_*`` keys are read at runtime anymore. The
    whole section is dead config — any presence of it (even an empty
    ``cms:`` block) fails startup instead of being silently ignored.
    """
    if "cms" not in config:
        return
    cms = config["cms"]
    keys = sorted(cms) if isinstance(cms, dict) else []
    detail = f" (keys: {', '.join(f'cms.{key}' for key in keys)})" if keys else ""
    raise ValueError(
        f"Unsupported yaml section: cms{detail}. The yaml cms section was "
        "retired (config governance G2), and the env CMS_* channel followed: "
        "CMS credentials now live on the instance-level external connection "
        "(admin settings → 外部服务连接), migrated automatically on first "
        "startup after upgrade. Remove the cms section from the yaml."
    )


def _reject_retired_agent_yaml_keys(config: dict[str, Any]) -> None:
    """Fail fast when the yaml still carries the retired Agent catalog keys.

    Agent config governance (phase 3, breaking): the yaml ``agents:`` catalog
    and the ``workflows.pi`` runtime block are no longer read. Agent
    definitions live in the DB (versioned_entities, managed in Studio →
    Agents); provider/model/thinking resolve from workspace Settings defaults
    or Studio node overrides. This check runs before env overrides so
    env-injected in-memory values are not mistaken for yaml keys.
    """
    retired: list[str] = []
    if "agents" in config:
        retired.append("agents")
    workflows = config.get("workflows")
    if isinstance(workflows, dict) and "pi" in workflows:
        retired.append("workflows.pi")
    if not retired:
        return
    keys = ", ".join(retired)
    raise ValueError(
        f"Unsupported yaml keys: {keys}. The yaml agents catalog and "
        "workflows.pi runtime block were retired (agent config governance). "
        "Migrate: agent definitions -> Studio Agents manager (published into "
        "versioned_entities); provider/model/thinking -> workspace Settings "
        "'Agent 默认配置' or Studio node execution overrides."
    )
