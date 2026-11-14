"""Apply Host claim lease TTLs to the Worker's existing transfer timeout."""

from __future__ import annotations

from typing import Any

from worker.execution.heartbeat_batch import clamp_batch_interval
from worker.transfer_lease_controls import transfer_timeout_cap, validate_transfer_timeout


def apply_lease_ttl(client: Any, lease_ttl_seconds: float) -> None:
    """Clamp transfers before a claimed execution can start or report.

    All claims in a Host batch share one TTL. The caller validates the complete
    batch before applying its minimum, so an off-contract item cannot partially
    change the live client.
    """
    cap = transfer_timeout_cap(lease_ttl_seconds, client.heartbeat_interval)
    configured = validate_transfer_timeout(client.configured_transfer_timeout)
    # A newer, larger Host TTL must not relax reports for claims already in
    # flight. The process-wide effective value therefore only ever tightens.
    effective = min(client.transfer_timeout, configured, cap)
    client.transfer_lease_ttl = float(lease_ttl_seconds)
    client.configured_transfer_timeout = configured
    client.transfer_timeout = effective
    _log_clamp(client, configured, float(lease_ttl_seconds), effective)


def update_heartbeat_interval(client: Any, interval: float) -> None:
    """Follow the relay's current config and re-clamp in-flight transfers."""
    client.heartbeat_interval = clamp_batch_interval(interval)
    ttl = getattr(client, "transfer_lease_ttl", None)
    if ttl is not None:
        apply_lease_ttl(client, ttl)


def sync_transfer_heartbeat(client: Any, transfer: Any, relay: bool) -> None:
    """Use hot state only in relay mode; bare executors keep their thread's cadence."""
    update_heartbeat_interval(
        client,
        transfer.heartbeat_interval_seconds if relay else getattr(client, "heartbeat_interval", 15),
    )


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
            f"transfer_timeout_seconds={configured:g} constrained to {effective:g}s "
            f"(current claim cap {raw_cap:g}s, TTL={ttl:g}, "
            f"heartbeat={client.heartbeat_interval:g}); clamping without relaxing in-flight reports",
            flush=True,
        )
