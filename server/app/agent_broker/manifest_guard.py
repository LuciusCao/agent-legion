"""Enqueue-time manifest guard for the Agent execution queue.

A request whose frozen manifest carries no routable provider/model can never
match a real Worker declaration: it would sit at the queue head forever,
silently blocking the workspace behind it (2026-08-01 incident, issue #13).
The broker rejects such manifests at enqueue instead — the producer surfaces
a node failure with an actionable message rather than a scheduling deadlock.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Template placeholders, not routable models: `your-model` is the historical
# config/workflow.yaml default that deadlocked the production queue.
PLACEHOLDER_MODELS = frozenset({"your-model"})


def require_routable_execution(manifest: Mapping[str, Any]) -> None:
    """Fail fast when the frozen manifest carries no routable provider/model."""
    if manifest.get("kind") == "code":
        # Code payloads carry no provider/model; routability is the code text
        # itself (hash-pinned bundle) plus the capability declaration.
        if not str(manifest.get("capability") or "") or not str(manifest.get("code_hash") or ""):
            raise ValueError("code request manifest requires a capability and a code_hash")
        return
    execution = manifest.get("execution") or {}
    provider = str(execution.get("provider") or "")
    model = str(execution.get("model") or "")
    if not provider or not model or model in PLACEHOLDER_MODELS:
        raise ValueError(
            f"Agent request manifest has unresolved provider/model "
            f"{provider!r}/{model!r}: no Worker could ever claim it; declare them "
            "via the node execution settings or the workspace defaults"
        )
