import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from server.app.configuration import load_application_config
from server.app.configuration.cors import CorsSettings, load_cors_settings
from server.app.configuration.instance_defaults import (
    apply_instance_config_defaults,
    resolve_worker_register_token,
)
from server.app.executors.runtime_config import (
    ExecutorRuntimeConfig,
    OpenClawRuntimeConfig,
    WorkflowsRuntimeConfig,
    validate_runtime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    root_dir: Path
    data_dir: Path
    videos_dir: Path
    logs_dir: Path
    packages_dir: Path
    jobs_dir: Path
    config: dict[str, Any]
    database_url: str = "postgresql://127.0.0.1:5432/agent_legion"
    cors: CorsSettings = field(default_factory=CorsSettings)
    executor_runtime: ExecutorRuntimeConfig = field(
        default_factory=lambda: ExecutorRuntimeConfig(
            workflows=WorkflowsRuntimeConfig(),
            openclaw=OpenClawRuntimeConfig(command_template=("openclaw",)),
        )
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    # override=False：已存在的环境变量优先于 .env 文件（与原手写实现一致）。
    load_dotenv(path, override=False)


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
# ``_apply_database_url_env`` below, which applies the authoritative
# AGENT_LEGION_DATABASE_URL override.
_ENV_OVERRIDES: dict[str, tuple[tuple[str, ...], Callable[[str], Any]]] = {
    "AGENT_LEGION_CUSTOM_NODES_ENABLED": (
        ("workflows", "custom_nodes_enabled"),
        _bool_parser,
    ),
    "AGENT_LEGION_OPENCLAW_CWD": (("openclaw", "cwd"), _path_parser),
    "AGENT_LEGION_WORKER_REGISTER_TOKEN": (("agent_workers", "register_token"), _str_parser),
    "AGENT_LEGION_WORKER_REGISTER_TOKEN_FILE": (
        ("agent_workers", "register_token_file"),
        _path_parser,
    ),
    "AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD": (("auth", "bootstrap_admin_password"), _str_parser),
    "AGENT_LEGION_CORS_ALLOW_ORIGINS": (("server", "cors", "allow_origins"), _csv_parser),
    "AGENT_LEGION_CORS_ALLOW_CREDENTIALS": (("server", "cors", "allow_credentials"), _bool_parser),
    "AGENT_LEGION_VAULT_MASTER_KEY": (("vault", "master_key"), _str_parser),
    "AGENT_LEGION_VAULT_MASTER_KEY_FILE": (("vault", "master_key_file"), _path_parser),
}

_DATABASE_URL_ENV = "AGENT_LEGION_DATABASE_URL"


def _apply_database_url_env(config: dict[str, Any]) -> None:
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


def _apply_env_overrides(config: dict[str, Any]) -> None:
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


def _reject_retired_cms_yaml_keys(config: dict[str, Any]) -> None:
    """Fail fast when the yaml ``cms:`` section carries retired keys.

    Config governance G2 (breaking): the CMS integration moved to
    instance-level external connections (admin settings → 外部服务连接);
    neither yaml nor env ``CMS_*`` keys are read at runtime anymore.
    """
    cms = config.get("cms")
    if not isinstance(cms, dict):
        return
    retired = [key for key in ("token", "token_gen") if key in cms]
    if not retired:
        return
    keys = ", ".join(f"cms.{key}" for key in retired)
    raise ValueError(
        f"Unsupported yaml keys under cms: {keys}. The yaml cms.token and "
        "cms.token_gen sections were retired (config governance G2), and the "
        "env CMS_* channel followed: CMS credentials now live on the "
        "instance-level external connection (admin settings → 外部服务连接), "
        "migrated automatically on first startup after upgrade."
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


def load_settings(data_dir: Path | None = None, config_path: Path | None = None) -> Settings:
    root_dir = PROJECT_ROOT
    if os.environ.get("AGENT_LEGION_SKIP_DOTENV") != "1":
        load_env_file(root_dir / ".env")
    loaded = load_application_config(root_dir, config_path=config_path)
    config = loaded.config
    _reject_retired_cms_yaml_keys(config)
    _reject_retired_agent_yaml_keys(config)
    _apply_database_url_env(config)
    _apply_env_overrides(config)
    apply_instance_config_defaults(config)
    if data_dir is None:
        env_data_dir = os.environ.get("AGENT_LEGION_DATA_DIR")
        if env_data_dir:
            data_dir = Path(env_data_dir)
    resolved_data_dir = data_dir or root_dir / str(config["data_dir"])
    database_url = str(config["database"]["url"])
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("database.url must be a PostgreSQL URL")
    videos_dir = resolved_data_dir / "videos"
    logs_dir = resolved_data_dir / "logs"
    packages_dir = resolved_data_dir / "packages"
    jobs_dir = resolved_data_dir / "jobs"
    for path in [resolved_data_dir, videos_dir, logs_dir, packages_dir, jobs_dir]:
        path.mkdir(parents=True, exist_ok=True)
    executor_runtime = ExecutorRuntimeConfig.model_validate(config)
    resolve_worker_register_token(executor_runtime.agent_workers, root_dir)
    return Settings(
        root_dir=root_dir,
        database_url=database_url,
        data_dir=resolved_data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config=config,
        cors=load_cors_settings(config),
        executor_runtime=executor_runtime,
    )


def validate_settings(settings: Settings) -> None:
    """Validate runtime dependencies after settings are constructed."""
    validate_runtime(settings.executor_runtime, settings.config)
