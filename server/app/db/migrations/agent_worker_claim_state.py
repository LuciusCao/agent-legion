"""Schema v83: ``agent_workers.claim_enabled`` — the Worker-reported claim switch.

A Worker with ``claim_enabled`` off still registers and keeps its liveness
fresh (status sync → ``/agent-workers/self``), so the Host UI showed it as
plainly「在线」 while every job sat in queued forever — the first support
question in the deployment checklist. The Worker now reports the switch on
every presence sync (and every claim implies ``true``); this column stores
the latest report. NULL = never reported (pre-upgrade Worker), which the UI
renders as the old plain「在线」.

Same guarded-ALTER home rule as v78/v80/v81: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay, and
both install paths run the chain anyway.
"""

from __future__ import annotations

from typing import Any

_CLAIM_STATE_DDL = """
alter table agent_workers
  add column if not exists claim_enabled boolean;
"""


def migrate_agent_worker_claim_state(conn: Any) -> None:
    """Add the nullable claim_enabled column (v83)."""
    conn.execute(_CLAIM_STATE_DDL)
