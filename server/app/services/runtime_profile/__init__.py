"""Runtime profile: stage-level bottleneck localization (#359).

L1 — six-stage pipeline gauges (intake / pass / enqueue / claim / execute /
result: depth + rate + latency) sampled into ``ops_runtime_profile_samples``
by the ops-metrics loop, plus DB-pool / advisory-lock cross-cutting waits.
#448 phase 1 adds the claim-transaction stage split (scan / evaluate /
writes totals + maxes, schema v78) — the worker claim loop is serial, so
one claim's round-trip is the throughput ceiling and the split orders the
follow-up work.

L2 — the bottleneck classifier (``classifier.py``) turns the latest gauges
plus the existing queue-alert signal (blocked/stalled, passed in as context
by the route) into one human-readable verdict with evidence, served by ``GET /api/metrics/runtime-profile``.

L3 (on-demand py-spy / pg_stat_statements) is deliberately not implemented
here — the issue scopes it as a separate deliverable.
"""

from server.app.services.runtime_profile.classifier import classify_bottleneck
from server.app.services.runtime_profile.counters import (
    RuntimeProfile,
    RuntimeProfileCounters,
    profile,
)
from server.app.services.runtime_profile.sampling import (
    persist_profile_sample,
    query_profile_series,
)

__all__ = [
    "RuntimeProfile",
    "RuntimeProfileCounters",
    "classify_bottleneck",
    "persist_profile_sample",
    "profile",
    "query_profile_series",
]
