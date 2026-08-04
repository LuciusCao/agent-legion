from __future__ import annotations

from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_registry import ResolverSpec
from server.app.settings import Settings


def resolve_candidates(
    spec: ResolverSpec,
    entity: str,
    input_values: list[str],
    source_kind: str,
    mode: Any,
    settings: Settings,
    workspace: dict[str, Any],
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Dispatch candidate resolution by the resolver's declared phase.

    ``phase="intake"`` handlers resolve via CMS during fan-out;
    ``phase="node"`` and direct (``phase=None``) handlers only build
    candidates — node-phase resolution happens at DAG execution time.
    """
    if spec.phase == "intake":
        return spec.handler(
            entity, input_values, source_kind, spec.key, mode, settings, workspace, workspace_id
        )
    if spec.phase in (None, "node"):
        return spec.handler(entity, input_values, source_kind)
    raise InvalidOperationError(f"Unsupported resolver: {spec.key}")
