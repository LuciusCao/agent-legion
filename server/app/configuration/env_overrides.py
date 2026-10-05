"""Environment-variable overrides for the settings trusted boundary.

Split out of ``settings.py`` (issue 287) so the override policy -- the
authoritative ``AGENT_LEGION_DATABASE_URL`` (config governance G4) plus the
reviewed env-to-config mapping -- lives in ``configuration/`` beside the other
trusted-boundary policy modules instead of inside the settings assembler,
which now only sequences load -> reject -> override -> defaults.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from server.app.configuration.worker_console import CONSOLE_URL_ENV, console_url_env


def _str_parser(value: str) -> str:
    return value


def _path_parser(value: str) -> str:
    """Expand ``~`` in path overrides while preserving command names unchanged."""
    return os.path.expanduser(value)


def _bool_parser(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"invalid boolean env value: {value!r}")


def _csv_parser(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _int_parser(value: str) -> int:
    """Parse an integer env override; ValueError fails the settings load."""
    return int(value)


# Reviewed mapping from environment variable to config path and parser.
# Do not add arbitrary double-underscore mutation; every override is listed here.
# ``database.url`` is deliberately absent: it is handled by
# ``apply_database_url_env`` below, which applies the authoritative
# AGENT_LEGION_DATABASE_URL override.
_ENV_OVERRIDES: dict[str, tuple[tuple[str, ...], Callable[[str], Any]]] = {
    "AGENT_LEGION_CUSTOM_NODES_ENABLED": (
        ("workflows", "custom_nodes_enabled"),
        _bool_parser,
    ),
    # #628: byte budget for one custom node code version (default 64KB).
    # #786: instance-settings managed (admin 全局设置); the env value remains
    # the default source until the stored document carries the key.
    # int() raises ValueError on garbage, which surfaces at settings load
    # (fail-fast); the ge=1024 bound is enforced by ExecutorRuntimeConfig.
    "AGENT_LEGION_NODE_CODE_MAX_BYTES": (
        ("workflows", "node_code_max_bytes"),
        _int_parser,
    ),
    # AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE) removed with the global token
    # retirement (issue #35): registration is scoped-token-only now. A leftover
    # variable must fail loudly at load time instead of silently ignoring a
    # credential the operator still believes is active.
    "AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD": (("auth", "bootstrap_admin_password"), _str_parser),
    # #738: per-token request bucket for workspace API tokens (instance-wide
    # refill rate per minute + burst capacity; defaults 60 / 20 in
    # auth/api_token_limits.py). env-only like the rest of ``auth``; a
    # non-integer or < 1 value fails the startup.
    "AGENT_LEGION_API_TOKEN_RATE_LIMIT_PER_MINUTE": (
        ("auth", "api_token_rate_limit_per_minute"),
        _int_parser,
    ),
    "AGENT_LEGION_API_TOKEN_RATE_LIMIT_BURST": (
        ("auth", "api_token_rate_limit_burst"),
        _int_parser,
    ),
    "AGENT_LEGION_CORS_ALLOW_ORIGINS": (("server", "cors", "allow_origins"), _csv_parser),
    "AGENT_LEGION_CORS_ALLOW_CREDENTIALS": (("server", "cors", "allow_credentials"), _bool_parser),
    # #989: roll the document CSP script-src back to 'unsafe-inline' (published
    # preview panels with inline onclick= handlers); see configuration/csp.py.
    "AGENT_LEGION_CSP_SCRIPT_UNSAFE_INLINE": (
        ("server", "csp", "script_unsafe_inline"),
        _bool_parser,
    ),
    "AGENT_LEGION_VAULT_MASTER_KEY": (("vault", "master_key"), _str_parser),
    "AGENT_LEGION_VAULT_MASTER_KEY_FILE": (("vault", "master_key_file"), _path_parser),
    "AGENT_LEGION_SKILLS_RUNS_DIR": (("skills", "runs_dir"), _path_parser),
    # 主控制台里「打开 Worker 控制台」链接的地址（部署拓扑，env-only；见
    # AgentWorkersRuntimeConfig.console_url）。
    "AGENT_LEGION_WORKER_CONSOLE_URL": (("agent_workers", "console_url"), _str_parser),
}

_DATABASE_URL_ENV = "AGENT_LEGION_DATABASE_URL"


def apply_database_url_env(config: dict[str, Any]) -> None:
    """Apply the database URL env override (config governance G4).

    ``AGENT_LEGION_DATABASE_URL`` is the single authoritative variable.
    """
    value = os.environ.get(_DATABASE_URL_ENV)
    if value is None:
        return
    database = config.setdefault("database", {})
    if not isinstance(database, dict):
        config["database"] = database = {}
    database["url"] = value


def apply_env_overrides(config: dict[str, Any]) -> None:
    """Apply known environment variable overrides before typed validation."""
    for env_var, (path, parser) in _ENV_OVERRIDES.items():
        raw = console_url_env() if env_var == CONSOLE_URL_ENV else os.environ.get(env_var)
        if raw is None:
            continue
        node = config
        for key in path[:-1]:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node[path[-1]] = parser(raw)
