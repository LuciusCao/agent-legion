"""Declarative intake resolver registry.

New content types plug in with one provider yaml declaration, one registry
entry, and one DAG capability — no framework code changes. ``phase`` declares
when external resolution happens: ``"intake"`` (the handler calls the external
service while fanning out), ``"node"`` (intake builds opaque candidates
carrying a ``source_ref``; a DAG node resolves them at execution time), or
``None`` (direct input, no resolution).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake_resolution import (
    resolve_direct_candidates,
    resolve_opaque_candidates,
)

if TYPE_CHECKING:
    from server.app.settings import Settings


@dataclass(frozen=True)
class ResolverSpec:
    """Declarative intake resolver registry entry.

    ``resource_key`` is the resource provider binding the resolver/node
    resolves against, if any.
    """

    key: str
    entity: str
    phase: str | None
    resource_key: str | None
    handler: Callable[..., list[dict[str, Any]]]


RESOLVERS: dict[tuple[str, str], ResolverSpec] = {
    ("question", "direct_ids"): ResolverSpec(
        "direct.question_ids", "question", None, None, resolve_direct_candidates
    ),
    # External-reference modes: intake fans out opaque candidates carrying
    # source_ref; the first DAG node resolves them against its configured
    # connection at execution time (phase="node"). Mode keys exist so that
    # workflow definitions declaring them stay valid without platform
    # coupling to any concrete external service.
    ("question", "batch_by_ids"): ResolverSpec(
        "node.question_ids", "question", "node", None, resolve_opaque_candidates
    ),
    ("question", "batch_by_knowledge"): ResolverSpec(
        "node.questions_by_knowledge", "question", "node", None, resolve_opaque_candidates
    ),
    ("question", "by_knowledge"): ResolverSpec(
        "node.question_by_knowledge", "question", "node", None, resolve_opaque_candidates
    ),
    ("video", "direct_ids"): ResolverSpec(
        "direct.video_ids", "video", None, None, resolve_direct_candidates
    ),
    ("video", "batch_by_urls"): ResolverSpec(
        "direct.video_urls", "video", None, None, resolve_direct_candidates
    ),
    ("video", "batch_by_knowledge"): ResolverSpec(
        "node.knowledge_video", "video", "node", None, resolve_opaque_candidates
    ),
    ("video", "by_knowledge"): ResolverSpec(
        "node.videos_by_knowledge", "video", "node", None, resolve_opaque_candidates
    ),
}


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
