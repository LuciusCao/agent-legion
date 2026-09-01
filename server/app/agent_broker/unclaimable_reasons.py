"""Claimability probes for the unclaimable-request sweeper.

Split out of ``unclaimable.py`` for the file-size budget; pure matching
logic kept separate from the sweep transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.agent_broker import agent_claim_compatibility

# Per-worker declarations: (runtimes, models). Claimability is judged per
# Worker — claim.py requires a single Worker to satisfy runtime and model
# together — so cross-Worker unions would report "runnable" for combinations
# no machine can claim; unions only phrase reasons. Capabilities no longer
# gate claims (issue #284).
WorkerDeclarations = tuple[set[str], set[tuple[str, str, str]]]


def unmatched_reasons(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    workers: Sequence[WorkerDeclarations],
) -> list[str]:
    """Why no Worker can run the candidate; empty = runnable by some Worker.

    Runnable iff one Worker declares runtime and model together. Otherwise
    probe ``worker_can_run`` over the model-declaration union so matching
    semantics (wildcards included) stay defined in exactly one place; a
    combination no single Worker covers reports its own reason. An empty
    provider/model never matches a concrete declaration.
    """
    can_run = agent_claim_compatibility.worker_can_run
    runtime = str(candidate["runtime"])
    union_runtimes: set[str] = set()
    union_models_any_runtime: set[tuple[str, str, str]] = set()
    for runtimes, models in workers:
        if runtime in runtimes and can_run(candidate, manifest, models):
            return []
        union_runtimes |= runtimes
        union_models_any_runtime |= {("*", provider, model) for _, provider, model in models}
    reasons: list[str] = []
    if runtime not in union_runtimes:
        reasons.append(f"runtime {runtime!r} not declared by any Worker")
    if not can_run(candidate, manifest, union_models_any_runtime):
        execution = manifest.get("execution") or {}
        provider = str(execution.get("provider") or "")
        model = str(execution.get("model") or "")
        reasons.append(f"model {provider}/{model} not declared by any Worker")
    if not reasons:
        reasons.append("no single Worker declares runtime and model together")
    return reasons
