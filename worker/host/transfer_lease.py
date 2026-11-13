"""Apply Host claim lease TTLs to the Worker's existing transfer timeout."""

from __future__ import annotations

from typing import Any

from worker.transfer_lease_controls import transfer_timeout_cap, validate_transfer_timeout


def apply_lease_ttl(client: Any, lease_ttl_seconds: float) -> None:
    """Clamp transfers before a claimed execution can start or report.

    All claims in a Host batch share one TTL. The caller validates the complete
    batch before applying its minimum, so an off-contract item cannot partially
    change the live client.
    """
    cap = transfer_timeout_cap(lease_ttl_seconds, client.heartbeat_interval)
    configured = validate_transfer_timeout(client.configured_transfer_timeout)
    effective = min(configured, cap)
    client.transfer_lease_ttl = float(lease_ttl_seconds)
    client.configured_transfer_timeout = configured
    client.transfer_timeout = effective
    _log_clamp(client, configured, float(lease_ttl_seconds), effective)


def _log_clamp(client: Any, configured: float, ttl: float | None, effective: float) -> None:
    if ttl is None:
        return
    signature = (configured, ttl, client.heartbeat_interval, effective)
    if signature == getattr(client, "transfer_clamp_signature", None):
        return
    client.transfer_clamp_signature = signature
    raw_cap = 2.0 * ttl - client.heartbeat_interval
    if raw_cap < 1.0:
        print(
            f"claim lease_ttl_seconds={ttl:g} with heartbeat_interval_seconds="
            f"{client.heartbeat_interval:g} leaves no safe transfer window; "
            f"clamping transfer_timeout_seconds to {effective:g}s",
            flush=True,
        )
    elif effective < configured:
        print(
            f"transfer_timeout_seconds={configured:g} exceeds the claim lease cap "
            f"{effective:g}s (2*lease_ttl_seconds-heartbeat_interval_seconds, TTL={ttl:g}, "
            f"heartbeat={client.heartbeat_interval:g}); clamping",
            flush=True,
        )
