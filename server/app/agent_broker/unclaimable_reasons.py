"""Claimability probes for the unclaimable-request sweeper.

Split out of ``unclaimable.py`` for the file-size budget; pure matching
logic kept separate from the sweep transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app import agent_claim_compatibility


def unmatched_reasons(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    worker_capabilities: set[str],
    worker_models: set[tuple[str, str]],
    worker_runtimes: set[str],
) -> list[str]:
    """Why the Worker declarations cannot run the candidate; empty = runnable.

    Probes ``worker_can_run`` with a universal declaration for one dimension
    at a time, so the matching semantics (wildcards included) stay defined in
    exactly one place. An empty provider/model never matches a concrete Worker
    declaration and therefore reports as unclaimable. Runtime is judged by
    direct membership (claim.py: ``selected["runtime"] not in runtimes``):
    a definition runtime no non-revoked Worker declares reports its own reason
    instead of rotting in queued.
    """
    can_run = agent_claim_compatibility.worker_can_run
    runtime = str(candidate["runtime"])
    if runtime in worker_runtimes and can_run(
        candidate, manifest, worker_capabilities, worker_models
    ):
        return []
    reasons: list[str] = []
    # A universal declaration for one dimension isolates the other: if the
    # probe still fails, the real declarations mismatch on that dimension.
    if runtime not in worker_runtimes:
        reasons.append(f"runtime {runtime!r} not declared by any Worker")
    if not can_run(candidate, manifest, worker_capabilities, {("*", "*")}):
        reasons.append(f"capability {candidate['capability']!r} not declared by any Worker")
    if not can_run(candidate, manifest, {"*"}, worker_models):
        pi = manifest.get("pi") or {}
        provider = str(pi.get("provider") or "")
        model = str(pi.get("model") or "")
        reasons.append(f"model {provider}/{model} not declared by any Worker")
    return reasons
