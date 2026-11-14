"""Fail-closed target database and worktree guards for seed_from_prod."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from scripts.seed_from_prod_db import SeedError
from scripts.seed_from_prod_libpq import guard_loopback_conninfo
from scripts.seed_from_prod_objects import expected_dev_resource_names
from server.app.db.schema_guard import dsn_database_name

PROD_DB_NAME = "agent_legion"


def parse_dsn(dsn: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(dsn)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise SeedError("目标 DSN 必须是 postgresql:// URL（值已脱敏，不回显）")
    return parsed


def guard_target_dsn(dsn: str) -> None:
    """Refuse prod names and every non-loopback libpq connection candidate."""
    parse_dsn(dsn)
    dbname = dsn_database_name(dsn)
    if dbname == PROD_DB_NAME:
        raise SeedError(f"拒绝执行：目标库名 {dbname!r} 与生产库同名，疑似指向生产。")
    guard_loopback_conninfo(dsn)


def guard_target_repo(dsn: str, repo_root: Path) -> tuple[str, str]:
    guard_target_dsn(dsn)
    expected_db, expected_bucket = expected_dev_resource_names(repo_root)
    actual_db = dsn_database_name(dsn)
    if actual_db != expected_db:
        raise SeedError(
            f"拒绝执行：当前 worktree 的目标库必须是 {expected_db!r}，收到 {actual_db!r}。"
        )
    return expected_db, expected_bucket
