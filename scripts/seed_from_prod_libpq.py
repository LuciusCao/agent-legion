"""Fail-closed libpq network semantics for ``seed_from_prod``."""

import os

import psycopg.conninfo

from scripts.seed_from_prod_db import SeedError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _target_hosts(dsn: str) -> list[str]:
    params = psycopg.conninfo.conninfo_to_dict(dsn)
    hosts: list[str] = []
    for key, env in (("host", "PGHOST"), ("hostaddr", "PGHOSTADDR")):
        value = params.get(key)
        if value is None:
            value = os.environ.get(env) or None
        if value is not None:
            hosts.extend(str(value).split(","))
    return hosts


def guard_loopback_conninfo(dsn: str) -> None:
    service = psycopg.conninfo.conninfo_to_dict(dsn).get("service") or os.environ.get("PGSERVICE")
    if service:
        raise SeedError(
            "拒绝执行：seed 目标必须显式使用本机 loopback host；请移除 service/PGSERVICE。"
        )
    hosts = _target_hosts(dsn)
    if not hosts or any(host not in LOOPBACK_HOSTS for host in hosts):
        raise SeedError(
            f"拒绝执行：目标 host/hostaddr 不是本机回环地址，必须全部为 {sorted(LOOPBACK_HOSTS)}。"
        )
