import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

from server.app.agent_catalog import AgentDefinition, load_agent_definitions
from server.app.configuration import load_application_config
from server.app.configuration.cors import CorsSettings, load_cors_settings
from server.app.executors import registration as _registration  # noqa: F401
from server.app.executors.config import ExecutorConfig
from server.app.executors.definitions import load_executor_definitions
from server.app.executors.runtime_config import (
    ExecutorRuntimeConfig,
    OpenClawRuntimeConfig,
    WorkflowsRuntimeConfig,
    validate_runtime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    executor_definitions: dict[str, ExecutorConfig] = field(default_factory=dict)
    agent_definitions: dict[str, AgentDefinition] = field(default_factory=dict)
    executor_runtime: ExecutorRuntimeConfig = field(
        default_factory=lambda: ExecutorRuntimeConfig(
            workflows=WorkflowsRuntimeConfig(),
            openclaw=OpenClawRuntimeConfig(command_template=("openclaw",)),
        )
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def _str_parser(value: str) -> str:
    return value


def _path_parser(value: str) -> str:
    """Expand ``~`` in path overrides while preserving command names unchanged."""
    return os.path.expanduser(value)


# Reviewed mapping from environment variable to config path and parser.
# Do not add arbitrary double-underscore mutation; every override is listed here.
_ENV_OVERRIDES: dict[str, tuple[tuple[str, ...], Callable[[str], Any]]] = {
    "VIDEO_HIVE_DATABASE_URL": (("database", "url"), _str_parser),
    "VIDEO_HIVE_CMS_TOKEN": (("cms", "token"), _str_parser),
    "VIDEO_HIVE_CMS_TOKEN_GEN_SECRET": (("cms", "token_gen", "secret"), _str_parser),
    "VIDEO_HIVE_ASR_WHISPER_BINARY": (("asr", "whisper", "binary"), _path_parser),
    "VIDEO_HIVE_ASR_WHISPER_MODEL": (("asr", "whisper", "model"), _path_parser),
    "VIDEO_HIVE_ASR_SENSEVOICE_MODEL_DIR": (("asr", "sensevoice", "model_dir"), _path_parser),
    "VIDEO_HIVE_PI_BINARY": (("workflows", "pi", "binary"), _path_parser),
    "VIDEO_HIVE_OPENCLAW_CWD": (("openclaw", "cwd"), _path_parser),
    "AGENT_LEGION_WORKER_REGISTER_TOKEN": (("agent_workers", "register_token"), _str_parser),
    "AGENT_LEGION_WORKER_REGISTER_TOKEN_FILE": (
        ("agent_workers", "register_token_file"),
        _path_parser,
    ),
    "AGENT_LEGION_DATABASE_URL": (("database", "url"), _str_parser),
    "AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD": (("auth", "bootstrap_admin_password"), _str_parser),
}


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


def _apply_basecms_env_overrides(config: dict[str, Any]) -> None:
    """Apply BASECMS_* overrides that predate the VIDEO_HIVE_* overrides."""
    base_url = os.environ.get("BASECMS_BASE_URL")
    if not base_url:
        return
    cms = config.setdefault("cms", {})
    if not isinstance(cms, dict):
        return
    cms["base_url"] = base_url


def _normalize_cms_config(config: dict[str, Any]) -> None:
    """Derive legacy URL fields from base_url when present."""
    cms = config.get("cms")
    if not isinstance(cms, dict):
        return
    base_url = str(cms.get("base_url", "")).rstrip("/")
    if not base_url:
        return
    params: dict[str, str] = {
        "bank_version": str(cms.get("bank_version", "v5")),
        "country_id": str(cms.get("country_id", "1")),
        "subject_id": str(cms.get("subject_id", "2")),
    }
    if not cms.get("knowledge_url"):
        cms["knowledge_url"] = f"{base_url}/knowledge/detail?" + urlencode(params)
    if not cms.get("question_url"):
        cms["question_url"] = f"{base_url}/question/detail?" + urlencode(params)
    if not cms.get("question_detail_url"):
        cms["question_detail_url"] = cms["question_url"]
    list_params = {**params}
    if "page_size" in cms and cms["page_size"] not in (None, ""):
        list_params["page_size"] = str(cms["page_size"])
    else:
        list_params["page_size"] = "50"
    if not cms.get("question_list_url"):
        cms["question_list_url"] = f"{base_url}/question/list?" + urlencode(list_params)


def load_settings(data_dir: Path | None = None, config_path: Path | None = None) -> Settings:
    root_dir = PROJECT_ROOT
    if os.environ.get("VIDEO_HIVE_SKIP_DOTENV") != "1":
        load_env_file(root_dir / ".env")
    loaded = load_application_config(root_dir, config_path=config_path)
    config = loaded.config
    _apply_env_overrides(config)
    _apply_basecms_env_overrides(config)
    _normalize_cms_config(config)
    if data_dir is None:
        env_data_dir = os.environ.get("VIDEO_HIVE_DATA_DIR")
        if env_data_dir:
            data_dir = Path(env_data_dir)
    resolved_data_dir = data_dir or root_dir / str(config.get("data_dir", "data"))
    database_config = config.get("database", {})
    database_url = str(database_config.get("url", "")) if isinstance(database_config, dict) else ""
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("database.url must be a PostgreSQL URL")
    videos_dir = resolved_data_dir / "videos"
    logs_dir = resolved_data_dir / "logs"
    packages_dir = resolved_data_dir / "packages"
    jobs_dir = resolved_data_dir / "jobs"
    for path in [resolved_data_dir, videos_dir, logs_dir, packages_dir, jobs_dir]:
        path.mkdir(parents=True, exist_ok=True)
    executor_definitions = cast(dict[str, ExecutorConfig], load_executor_definitions(config.get("executors", {})))  # fmt: skip
    agent_definitions = load_agent_definitions(config.get("agents", {}))
    executor_runtime = ExecutorRuntimeConfig.model_validate(config)
    token_file = executor_runtime.agent_workers.register_token_file
    if token_file and not executor_runtime.agent_workers.register_token:
        executor_runtime.agent_workers.register_token = (
            Path(token_file).read_text(encoding="utf-8").strip()
        )
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
        executor_definitions=executor_definitions,
        agent_definitions=agent_definitions,
        executor_runtime=executor_runtime,
    )


def validate_settings(settings: Settings) -> None:
    """Validate runtime dependencies after settings are constructed."""
    validate_runtime(settings.executor_runtime, settings.config, settings.executor_definitions)
