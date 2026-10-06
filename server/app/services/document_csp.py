"""Document CSP compatibility switch (#989), an admin instance setting.

``csp_script_unsafe_inline`` in the instance settings document (admin
「全局设置 → 实例设置 → 安全」, default off) rolls the document policy's
``script-src`` back to the pre-#989 ``'self' 'unsafe-inline'``: the escape
hatch for published preview panels that still rely on inline event-handler
attributes (``onclick=``) or ``javascript:`` URLs. Policy rationale in
server/app/http_csp.py.

Unlike the restart-hydrated instance scalars, the switch is read at serve
time by the SPA route (every index.html response) — so it must not cost a
DB round trip per page load. ``CspCompatSwitch`` caches the value for
``ttl_seconds`` (5 s): the admin PUT route invalidates this process's cache
directly, so a save applies on the very next page load; the TTL bounds the
lag for out-of-band writes (another process, direct DB edits). There is one
instance per app (``app.state.csp_compat``), never module-global state.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg

from server.app.db.dialect import ConnectSource
from server.app.services.instance_settings_store import InstanceSettingsStore

logger = logging.getLogger(__name__)

CSP_COMPAT_SETTING_KEY = "csp_script_unsafe_inline"
DEFAULT_TTL_SECONDS = 5.0


class CspCompatSwitch:
    """Cached read of the compatibility switch; strict on any doubt."""

    def __init__(
        self,
        connect_source: ConnectSource,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = InstanceSettingsStore(connect_source)
        self._ttl = ttl_seconds
        self._clock = clock
        # (value, expires_at); None = nothing cached. One tuple swap keeps
        # concurrent threadpool readers consistent without a lock (a race
        # costs at most one extra read).
        self._cached: tuple[bool, float] | None = None

    def enabled(self) -> bool:
        cached = self._cached
        now = self._clock()
        if cached is not None and now < cached[1]:
            return cached[0]
        try:
            stored = self._store.get()
        except psycopg.Error:
            # Fail closed to the strict policy and do not cache the miss, so
            # the next page load retries. Strict is the default anyway; only
            # instances relying on the switch see inline handlers blocked
            # until the DB answers again.
            logger.warning("csp compat switch read failed; serving strict policy", exc_info=True)
            return False
        value = (stored or {}).get(CSP_COMPAT_SETTING_KEY) is True
        self._cached = (value, now + self._ttl)
        return value

    def invalidate(self) -> None:
        self._cached = None
