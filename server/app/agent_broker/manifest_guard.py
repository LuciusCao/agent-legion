"""Enqueue-time manifest guard for the Agent execution queue.

A request whose frozen manifest carries no routable model can never match a
real Worker declaration: it would sit at the queue head forever, silently
blocking the workspace behind it (2026-08-01 incident, issue #13). The
broker rejects such manifests at enqueue instead — the producer surfaces a
node failure with an actionable message rather than a scheduling deadlock.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Template placeholders, not routable models: `your-model` is the historical
# config/workflow.yaml default that deadlocked the production queue.
PLACEHOLDER_PI_MODELS = frozenset({"your-model"})


def require_routable_pi_model(manifest: Mapping[str, Any]) -> None:
    """Fail fast when the frozen manifest carries no routable model."""
    model = str((manifest.get("pi") or {}).get("model") or "")
    if not model or model in PLACEHOLDER_PI_MODELS:
        raise ValueError(
            f"Agent request manifest has unresolved model {model!r}: no Worker could"
            " ever claim it; declare the model via the node/agent execution settings"
        )
