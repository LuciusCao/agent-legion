"""Queue-alert classification for the ops-metrics summary (issue #13).

Split out of the queue summary for the file-size budget; pure
decision logic kept separate from the summary queries:
``blocked`` = a fresh signal from the empty-claim trigger (claims attempted,
every candidate skipped — histogram carried in ``reasons``); ``stalled`` =
queued rows with zero running executions and no claim attempts at all (e.g.
the only compatible Worker stopped pulling).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.services.ops_metrics.series import _isoformat_utc

# A blocked signal fresher than this drives the red banner; older signals are
# considered recovered and self-expire (nobody ever clears the row).
_BLOCKED_SIGNAL_FRESH = timedelta(minutes=10)
# Grace window before a non-zero queue with zero activity counts as stalled:
# absorbs the enqueue → next-worker-poll gap and brief sampler pauses.
_STALLED_MIN_AGE = timedelta(minutes=2)


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def queue_alert(
    *,
    now: datetime,
    queued: int,
    oldest: Any,
    active: int,
    online: int,
    signal: Any,
) -> dict[str, Any] | None:
    if signal is not None and queued > 0:
        updated_at = _aware(signal["updated_at"])
        if now - updated_at <= _BLOCKED_SIGNAL_FRESH:
            return {
                "kind": str(signal["kind"]),
                "at": _isoformat_utc(updated_at),
                "reasons": {
                    str(key): int(value)
                    for key, value in json.loads(str(signal["reasons_json"] or "{}")).items()
                },
            }
    if (
        queued > 0
        and active == 0
        and online > 0
        and oldest is not None
        and now - _aware(oldest) >= _STALLED_MIN_AGE
    ):
        return {"kind": "stalled", "at": None, "reasons": {}}
    return None
