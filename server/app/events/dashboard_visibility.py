"""Per-connection visibility filter for the dashboard SSE stream (#881).

The ``dashboard`` bus channel is one broadcast: every ``workspace_stats_batch``
carries the stats of every workspace that changed. A restricted subscriber
(non-admin, or a workspace-bound token) must only ever see entries for the
workspaces it may list (#711's rule, ``workspace_visibility_scope``), or the
stream becomes a workspace-id enumeration oracle.

The visible set is cached on the connection — the broadcast fan-out never
queries the DB per event. Membership changes during a long-lived connection
are picked up lazily: when a batch arrives and the cache is older than
``refresh_seconds``, the set is re-resolved once (off the event loop). A
reconnect always resolves afresh. Admin status itself is decided at connect
time; unrestricted identities get no filter at all.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

from server.app.events.dashboard import build_workspace_stats_batch_payload

DEFAULT_REFRESH_SECONDS = 30.0


class DashboardStatsFilter:
    """Narrows dashboard payloads to the connection's visible workspaces.

    Returns the rewritten payload, or None to drop it: a batch whose
    entries are all invisible is dropped (no empty events), and payload
    types other than ``workspace_stats_batch`` are dropped too — a
    restricted connection fails closed on anything it cannot vet.
    """

    def __init__(
        self,
        resolve_visible: Callable[[], frozenset[str]],
        visible: frozenset[str],
        *,
        refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolve_visible = resolve_visible
        self._visible = visible
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._resolved_at = clock()

    async def __call__(self, payload: str) -> str | None:
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("type") != "workspace_stats_batch":
            return None
        if self._clock() - self._resolved_at >= self._refresh_seconds:
            self._visible = await asyncio.to_thread(self._resolve_visible)
            self._resolved_at = self._clock()
        entries = [
            entry
            for entry in data.get("workspaces") or []
            if isinstance(entry, dict) and str(entry.get("id")) in self._visible
        ]
        if not entries:
            return None
        return build_workspace_stats_batch_payload(int(data.get("latest_revision") or 0), entries)
