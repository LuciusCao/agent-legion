"""Declarative intake resolver registry.

New content types plug in with one provider yaml declaration, one registry
entry, and one DAG capability — no framework code changes. ``phase`` declares
when CMS resolution happens: ``"intake"`` (the handler calls CMS while fanning
out), ``"node"`` (intake builds opaque candidates carrying a ``source_ref``;
a DAG node resolves them at execution time), or ``None`` (direct input, no
resolution).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import InvalidOperationError, UnsupportedOperationError
from server.app.services.job_intake_resolution import (
    resolve_cms_question_opaque_candidates,
    resolve_direct_candidates,
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


def _resolve_cms_knowledge_video_candidates(
    entity: str,
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    # Late import: job_intake_video imports ``candidate`` from
    # job_intake_resolution.
    from server.app.services.job_intake_video import resolve_cms_video_candidates

    return resolve_cms_video_candidates(entity, input_values, source_kind)


def _unsupported_videos_by_knowledge(
    entity: str,
    input_values: list[str],
    source_kind: str,
) -> list[dict[str, Any]]:
    raise UnsupportedOperationError("Unsupported video resolver: cms.videos_by_knowledge")


RESOLVERS: dict[tuple[str, str], ResolverSpec] = {
    ("question", "direct_ids"): ResolverSpec(
        "direct.question_ids", "question", None, None, resolve_direct_candidates
    ),
    ("question", "by_knowledge"): ResolverSpec(
        "cms.questions_by_knowledge",
        "question",
        "node",
        "by_knowledge",
        resolve_cms_question_opaque_candidates,
    ),
    ("question", "batch_by_ids"): ResolverSpec(
        "cms.question_ids",
        "question",
        "node",
        "question_detail",
        resolve_cms_question_opaque_candidates,
    ),
    ("question", "batch_by_knowledge"): ResolverSpec(
        "cms.questions_by_knowledge",
        "question",
        "node",
        "by_knowledge",
        resolve_cms_question_opaque_candidates,
    ),
    ("video", "direct_ids"): ResolverSpec(
        "direct.video_ids", "video", None, None, resolve_direct_candidates
    ),
    ("video", "by_knowledge"): ResolverSpec(
        "cms.videos_by_knowledge", "video", "node", None, _unsupported_videos_by_knowledge
    ),
    ("video", "batch_by_urls"): ResolverSpec(
        "direct.video_urls", "video", None, None, resolve_direct_candidates
    ),
    ("video", "batch_by_knowledge"): ResolverSpec(
        "cms.knowledge_video",
        "video",
        "node",
        "knowledge_video",
        _resolve_cms_knowledge_video_candidates,
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
