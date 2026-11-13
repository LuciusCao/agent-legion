"""Fail-closed target database and worktree guards for seed_from_prod."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from scripts.seed_from_prod_db import SeedError
from scripts.seed_from_prod_objects import expected_dev_resource_names
from server.app.db.schema_guard import dsn_database_name

PROD_DB_NAME = "agent_legion"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def parse_dsn(dsn: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(dsn)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise SeedError("目标 DSN 必须是 postgresql:// URL（值已脱敏，不回显）")
    return parsed


def guard_target_dsn(dsn: str) -> None:
    """Refuse to run against anything that looks like the prod database."""
    parsed = parse_dsn(dsn)
    host = parsed.hostname or ""
    # libpq semantics cover URL-decoding, ?dbname= and PGDATABASE; urlparse alone is insufficient.
    dbname = dsn_database_name(dsn)
    if dbname == PROD_DB_NAME:
        raise SeedError(f"拒绝执行：目标库名 {dbname!r} 与生产库同名，疑似指向生产。")
    if host not in LOOPBACK_HOSTS:
        raise SeedError(f"拒绝执行：目标 host {host!r} 不是本机回环地址 {sorted(LOOPBACK_HOSTS)}。")


def guard_target_repo(dsn: str, repo_root: Path) -> tuple[str, str]:
    guard_target_dsn(dsn)
    expected_db, expected_bucket = expected_dev_resource_names(repo_root)
    actual_db = dsn_database_name(dsn)
    if actual_db != expected_db:
        raise SeedError(
            f"拒绝执行：当前 worktree 的目标库必须是 {expected_db!r}，收到 {actual_db!r}。"
        )
    return expected_db, expected_bucket


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
