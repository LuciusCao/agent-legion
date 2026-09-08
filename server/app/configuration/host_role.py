"""Host process role for the control-plane role split (#521 方案 B).

``AGENT_LEGION_HOST_ROLE`` selects what one Host process runs:

- ``combined`` (default): the pre-split single process — HTTP API plane
  plus the scheduler plane (sweeper, workflow worker, slow sweeps,
  ops-metrics sampling) in one uvicorn process. The single-process
  deployment shape stays the default and stays supported.
- ``http``: the API plane only. ``create_prod_app`` composes with
  ``start_worker=False``-equivalent semantics: no scheduler threads, no
  sampling loop; wakeup notifications from the write paths are relayed to
  the scheduler plane via PostgreSQL ``NOTIFY`` (see
  ``server/app/scheduler_notify.py``).
- ``scheduler``: the scheduler plane only. Started via the
  ``python -m server.app.scheduler_process`` entry (NOT a uvicorn app):
  sweeper + workflow worker + slow sweeps + ops-metrics sampling in a
  dedicated process, so result-commit waves on the HTTP plane cannot
  starve the claim/heartbeat loop under the GIL.

The role is env-only (like the DB pool knobs): it is a per-process
deployment concern, not an instance-level tunable — two processes of one
deployment must agree on the database but pick their roles individually,
so the DB instance-settings document (shared by every process of the
deployment) is the wrong home for it.
"""

from __future__ import annotations

import os

ROLE_ENV = "AGENT_LEGION_HOST_ROLE"

ROLE_COMBINED = "combined"
ROLE_HTTP = "http"
ROLE_SCHEDULER = "scheduler"

VALID_ROLES = (ROLE_COMBINED, ROLE_HTTP, ROLE_SCHEDULER)


def host_role_from_env(default: str = ROLE_COMBINED) -> str:
    """Read and validate ``AGENT_LEGION_HOST_ROLE``.

    An invalid value raises ``ValueError`` at startup (fail-fast): a typo
    silently falling back to ``combined`` would run two schedulers against
    one database — the exact silent-degradation failure mode the role
    split exists to make explicit.
    """
    raw = os.environ.get(ROLE_ENV, "").strip().lower() or default
    if raw not in VALID_ROLES:
        raise ValueError(f"{ROLE_ENV} must be one of {', '.join(VALID_ROLES)} (got {raw!r})")
    return raw
