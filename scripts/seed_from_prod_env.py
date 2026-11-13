"""Environment-file DSN resolution for ``seed_from_prod``."""

from pathlib import Path

from scripts.seed_from_prod_db import SeedError


def read_env_file_database_url(env_file: Path) -> str | None:
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "AGENT_LEGION_DATABASE_URL":
            return value.strip().strip('"').strip("'")
    return None


def resolve_target_dsn(environ: dict[str, str], env_file: Path) -> str:
    dsn = environ.get("AGENT_LEGION_DATABASE_URL") or read_env_file_database_url(env_file)
    if not dsn:
        raise SeedError("找不到目标库 DSN：请设置 AGENT_LEGION_DATABASE_URL 或写进 .env。")
    return dsn
