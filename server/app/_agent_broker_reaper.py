"""Bundle-dir garbage collection for terminal Agent executions.

Split out of ``agent_broker.py`` so the broker module only carries the queue
protocol; mirrors the ``executors/_lease_*.py`` layout.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.agent_broker import AgentExecutionBroker

_SAFE_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def reap_terminal_bundles(
    broker: AgentExecutionBroker, *, archive_max_age_seconds: float = 3600
) -> int:
    """Reclaim bundle-dir files that no live execution can still need.

    The result route only deletes the shared execution bundle after a
    fully committed result, so failure paths (409/500, crashes) leave
    bundles of terminal requests and orphaned per-attempt result archives
    behind. This is the GC half of that contract."""
    if broker.bundle_dir is None:
        return 0
    reaped = 0
    with read_connection(broker.database_dsn) as conn:
        rows = conn.execute(
            "select manifest_json from agent_execution_requests"
            " where state in ('done', 'cancelled')"
        ).fetchall()
    for row in rows:
        try:
            manifest = json.loads(row["manifest_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        bundle_name = str(manifest.get("bundle_name", ""))
        if _SAFE_BUNDLE_NAME.fullmatch(bundle_name):
            target = broker.bundle_dir / bundle_name
            if target.is_file():
                target.unlink(missing_ok=True)
                reaped += 1
    cutoff = time.time() - archive_max_age_seconds
    for orphan in broker.bundle_dir.glob("*.result.tar.gz"):
        try:
            if orphan.stat().st_mtime < cutoff:
                orphan.unlink(missing_ok=True)
                reaped += 1
        except OSError:
            continue
    return reaped
