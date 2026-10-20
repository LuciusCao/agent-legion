"""Env-tunable pool sizing and recycling knobs (split out of pools.py, #1077)."""

from __future__ import annotations

import os

POOL_MAX_SIZE_ENV = "AGENT_LEGION_DB_POOL_MAX_SIZE"
POOL_MAX_IDLE_ENV = "AGENT_LEGION_DB_POOL_MAX_IDLE"
POOL_MAX_LIFETIME_ENV = "AGENT_LEGION_DB_POOL_MAX_LIFETIME"
DEFAULT_POOL_MAX_SIZE = 32
# Recycling defaults (120s idle / 900s lifetime) are tighter than
# psycopg-pool's built-in 600s/3600s: idle shrink closes at most one
# connection per max_idle interval, and a backend's session memory
# (plan/sort caches) only returns to the OS when the connection is recycled,
# so long-lived connections balloon under sustained load.
DEFAULT_POOL_MAX_IDLE = 120.0
DEFAULT_POOL_MAX_LIFETIME = 900.0


def pool_max_size() -> int:
    try:
        return max(1, int(os.environ.get(POOL_MAX_SIZE_ENV, "")))
    except ValueError:
        return DEFAULT_POOL_MAX_SIZE


def _pool_seconds(env: str, default: float) -> float:
    try:
        return max(1.0, float(os.environ.get(env, "")))
    except ValueError:
        return default


def pool_max_idle() -> float:
    return _pool_seconds(POOL_MAX_IDLE_ENV, DEFAULT_POOL_MAX_IDLE)


def pool_max_lifetime() -> float:
    return _pool_seconds(POOL_MAX_LIFETIME_ENV, DEFAULT_POOL_MAX_LIFETIME)
