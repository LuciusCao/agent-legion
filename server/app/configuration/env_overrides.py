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
    "AGENT_LEGION_OPENCLAW_CWD": (("openclaw", "cwd"), _path_parser),
    # AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE) removed with the global token
    # retirement (issue #35): registration is scoped-token-only now. A leftover
    # variable must fail loudly at load time instead of silently ignoring a
    # credential the operator still believes is active.
    "AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD": (("auth", "bootstrap_admin_password"), _str_parser),
    "AGENT_LEGION_CORS_ALLOW_ORIGINS": (("server", "cors", "allow_origins"), _csv_parser),
    "AGENT_LEGION_CORS_ALLOW_CREDENTIALS": (("server", "cors", "allow_credentials"), _bool_parser),
    "AGENT_LEGION_VAULT_MASTER_KEY": (("vault", "master_key"), _str_parser),
    "AGENT_LEGION_VAULT_MASTER_KEY_FILE": (("vault", "master_key_file"), _path_parser),
    "AGENT_LEGION_SKILLS_RUNS_DIR": (("skills", "runs_dir"), _path_parser),
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
        raw = os.environ.get(env_var)
        if raw is None:
            continue
        node = config
        for key in path[:-1]:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node[path[-1]] = parser(raw)
