"""Pure validation for the Worker transfer/claim-lease safety invariant."""

from __future__ import annotations

import math
from typing import Any


def validate_transfer_timeout(value: Any) -> float:
    """Return a finite positive timeout, or reject it with a config diagnostic."""
    if isinstance(value, bool):
        raise ValueError("transfer_timeout_seconds 必须是正的有限数")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("transfer_timeout_seconds 必须是正的有限数") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("transfer_timeout_seconds 必须是正的有限数")
    return timeout


def transfer_timeout_cap(lease_ttl_seconds: Any, heartbeat_interval: Any) -> float:
    """Bound one report attempt inside the two-beat lease-recovery window.

    This is the #1098 invariant made explicit: ``timeout <= 2*TTL - heartbeat``.
    Very short TTLs cannot satisfy that inequality with a practical timeout;
    they get a loud one-second floor rather than a non-positive requests value.
    """
    if isinstance(lease_ttl_seconds, bool) or isinstance(heartbeat_interval, bool):
        raise ValueError("claim lease TTL and heartbeat interval must be positive finite numbers")
    try:
        ttl = float(lease_ttl_seconds)
        heartbeat = float(heartbeat_interval)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "claim lease TTL and heartbeat interval must be positive finite numbers"
        ) from exc
    if not math.isfinite(ttl) or not math.isfinite(heartbeat) or ttl <= 0 or heartbeat <= 0:
        raise ValueError("claim lease TTL and heartbeat interval must be positive finite numbers")
    return max(1.0, 2.0 * ttl - heartbeat)
