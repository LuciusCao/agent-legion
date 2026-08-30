import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from server.app.configuration import load_application_config
from server.app.configuration.cors import CorsSettings, load_cors_settings
from server.app.configuration.env_overrides import apply_database_url_env, apply_env_overrides
from server.app.configuration.executor_runtime import (
    ExecutorRuntimeConfig,
    OpenClawRuntimeConfig,
    WorkflowsRuntimeConfig,
    validate_runtime,
)
from server.app.configuration.instance_defaults import apply_instance_config_defaults
from server.app.configuration.retired_config import reject_retired_config
from server.app.skills.paths import default_skills_runs_dir

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
    # Host-side scratch for skill snapshots/cache locks; must resolve
    # identically in every process sharing the skill cache (FileLock
    # domain) — per-process temp dirs pin it via AGENT_LEGION_SKILLS_RUNS_DIR.
    skills_runs_dir: Path = field(default_factory=default_skills_runs_dir)
    cors: CorsSettings = field(default_factory=CorsSettings)
    executor_runtime: ExecutorRuntimeConfig = field(
        default_factory=lambda: ExecutorRuntimeConfig(
            workflows=WorkflowsRuntimeConfig(),
            openclaw=OpenClawRuntimeConfig(),
        )
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    # override=False：已存在的环境变量优先于 .env 文件（与原手写实现一致）。
    load_dotenv(path, override=False)


def load_settings(data_dir: Path | None = None, config_path: Path | None = None) -> Settings:
    root_dir = PROJECT_ROOT
    if os.environ.get("AGENT_LEGION_SKIP_DOTENV") != "1":
        load_env_file(root_dir / ".env")
    loaded = load_application_config(root_dir, config_path=config_path)
    config = loaded.config
    reject_retired_config(config)
    apply_database_url_env(config)
    apply_env_overrides(config)
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
    # skills.runs_dir is env-only by contract (AGENT_LEGION_SKILLS_RUNS_DIR);
    # a hand-written yaml skills section would also land here (the loader
    # does not own a skills key), which is tolerated but unsupported.
    skills_override = config.get("skills", {}).get("runs_dir")
    return Settings(
        root_dir=root_dir,
        database_url=database_url,
        data_dir=resolved_data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config=config,
        skills_runs_dir=Path(skills_override) if skills_override else default_skills_runs_dir(),
        cors=load_cors_settings(config),
        executor_runtime=executor_runtime,
    )


def validate_settings(settings: Settings) -> None:
    """Validate runtime dependencies after settings are constructed."""
    validate_runtime(settings.executor_runtime, settings.config)
