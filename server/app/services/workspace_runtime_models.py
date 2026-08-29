"""Aggregate the (runtime, provider, model) triples of a workspace's online Workers.

Feeds the Studio node execution dropdowns: per-node provider/model inputs
offer the models the workspace's online Workers actually declared
(``agent_workers.models_json``), so a typed value corresponds to a Worker
that can claim the execution. Data access goes through the existing
``AgentWorkerRegistry`` facade — no SQL here (BOUNDARY-DATA-001).
"""

from __future__ import annotations

from typing import Any


def workspace_runtime_models(registry: Any, workspace_id: str) -> dict[str, dict[str, list[str]]]:
    """``{runtime: {provider: [models]}}`` from the workspace's online Workers.

    Only online Workers contribute (an offline Worker's models cannot claim
    anything right now). Models are deduplicated and sorted; the ``*``
    wildcard declarations pass through as-is.
    """
    aggregated: dict[str, dict[str, set[str]]] = {}
    for worker in registry.list_workers(workspace_id):
        if not worker.get("online"):
            continue
        for entry in worker.get("models") or []:
            runtime = str(entry.get("runtime") or "*")
            provider = str(entry.get("provider") or "")
            model = str(entry.get("model") or "")
            if not provider or not model:
                continue
            aggregated.setdefault(runtime, {}).setdefault(provider, set()).add(model)
    return {
        runtime: {provider: sorted(models) for provider, models in sorted(providers.items())}
        for runtime, providers in sorted(aggregated.items())
    }
