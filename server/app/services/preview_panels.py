"""Workspace preview panel bundles (schema v71, issue #328).

One HTML+CSS+JS single-file bundle per workspace customizes the job detail
left-column content panel. Bundles ride the unified draft → published →
archived lifecycle (``versioned_entities``, entity_type ``preview_panel``)
with the same governance as Studio node-code drafts (STUDIO-AGENT-001): the
studio agent reads state and writes drafts, publishing is always a human
action on the secured route surface.

The published bundle renders in an ``<iframe sandbox="allow-scripts">``
(never ``allow-same-origin``) and talks to the host page over the read-only
postMessage bridge (listArtifacts/readArtifact/getJobDetail + theme), so a
bundle — agent-authored by design — can never touch the user session or the
rest of the platform.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from server.app.db.dialect import ConnectSource
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import InvalidOperationError, JobServiceError, NotFoundError
from server.app.services.job_query_presenters import artifact_names
from server.app.services.versioned_entities import EntityType, VersionedEntity, VersionedEntityStore
from server.app.storage.s3_client import build_s3_storage

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.settings import Settings

# v1 scope: exactly one panel bundle per workspace (issue #328 — per-source_type
# keys can join later without changing the mechanism).
PANEL_ENTITY_KEY = "default"

# HTML+CSS+JS in one file: more generous than the node-code 64 KiB ceiling
# because the bundle carries its own markup, styles and logic.
MAX_BUNDLE_BYTES = 256 * 1024

# get_preview_context sampling defaults: enough for the agent to see real data
# shapes, bounded so the tool response stays small.
_RECENT_JOBS_LIMIT = 5
_SAMPLE_ARTIFACT_LIMIT = 5
_SAMPLE_MAX_CHARS = 2000

_ENTITY_TYPE: EntityType = "preview_panel"


def validate_panel_html(html: str) -> None:
    """Bundle contract: a non-empty full HTML document within the size budget."""
    if len(html.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise InvalidOperationError(
            f"preview panel bundle exceeds the {MAX_BUNDLE_BYTES}-byte size limit"
        )
    if not html.strip():
        raise InvalidOperationError("preview panel bundle must not be empty")
    if "<html" not in html.lower():
        raise InvalidOperationError(
            "preview panel bundle must be a full HTML document (missing <html>)"
        )


def bundle_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _to_row(entity: VersionedEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "workspace_id": entity.workspace_id,
        "entity_key": entity.entity_key,
        "version": entity.version,
        "status": entity.status,
        "html": entity.definition["html"],
        "html_hash": entity.definition_hash,
        "created_by": entity.created_by,
        "change_note": entity.definition.get("change_note"),
        "created_at": entity.created_at,
        "published_at": entity.published_at,
    }


class PreviewPanelService:
    """Versioned preview panel bundle storage and publish flow.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN
    (BOUNDARY-DATA-001, #187); it only feeds the VersionedEntityStore.
    """

    def __init__(self, database_dsn: ConnectSource) -> None:
        self._store = VersionedEntityStore(database_dsn, _ENTITY_TYPE)

    def get_published(self, workspace_id: str) -> dict[str, Any] | None:
        entity = self._store.get_published(PANEL_ENTITY_KEY, workspace_id)
        return _to_row(entity) if entity else None

    def get_draft(self, workspace_id: str) -> dict[str, Any] | None:
        for entity in self._store.list_versions(PANEL_ENTITY_KEY, workspace_id):
            if entity.status == "draft":
                return _to_row(entity)
        return None

    def get_state(self, workspace_id: str) -> dict[str, Any]:
        """Published bundle plus any pending draft (the Studio/human read)."""
        return {
            "published": self.get_published(workspace_id),
            "draft": self.get_draft(workspace_id),
        }

    def save_draft(
        self,
        workspace_id: str,
        html: str,
        created_by: str,
        change_note: str | None = None,
    ) -> dict[str, Any]:
        """Save or overwrite the workspace's draft bundle (single draft per workspace)."""
        validate_panel_html(html)
        entity = self._store.save_draft(
            PANEL_ENTITY_KEY,
            {"html": html, "change_note": change_note},
            bundle_hash(html),
            workspace_id,
            created_by,
        )
        return _to_row(entity)

    def publish(self, workspace_id: str) -> dict[str, Any]:
        """Publish the current draft; the previously published version archives."""
        return _to_row(self._store.publish(PANEL_ENTITY_KEY, workspace_id))

    def archive_all(self, workspace_id: str) -> int:
        """Archive every version (human "reset to the built-in fallback")."""
        return self._store.archive_all(PANEL_ENTITY_KEY, workspace_id)


def _job_summary(job: dict[str, Any], settings: Settings) -> dict[str, Any]:
    summary = {
        key: job.get(key)
        for key in ("id", "status", "source_type", "source_id", "created_at", "updated_at")
    }
    summary["artifacts"] = artifact_names(job, settings)
    return summary


def get_preview_context(
    job_db: JobQueries,
    settings: Settings,
    workspace_id: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Recent jobs, their artifact lists, and content samples of one job.

    The studio agent authors a panel against real data shapes: it needs the
    artifact inventory of recent jobs plus a bounded content sample. Reads go
    through the same local-first/object-fallback path as the artifact API
    (JobArtifactService), so worker-executed jobs sample correctly too.
    """
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    jobs = job_db.list_jobs(workspace_id=workspace_id, limit=_RECENT_JOBS_LIMIT)
    selected: dict[str, Any] | None = None
    if job_id is not None:
        selected = job_db.get_job(job_id)
        if selected is None or str(selected.get("workspace_id")) != workspace_id:
            raise NotFoundError("Job not found in this workspace")
    elif jobs:
        selected = jobs[0]

    samples: dict[str, str] = {}
    truncated: list[str] = []
    selected_summary: dict[str, Any] | None = None
    if selected is not None:
        object_store = JobArtifactObjectStore(job_db, build_s3_storage())
        artifacts = JobArtifactService(job_db, object_store)
        names = sorted(set(_job_summary(selected, settings)["artifacts"]))
        if object_store.enabled:
            names = sorted(set(names) | set(object_store.names_for_job(str(selected["id"]))))
        selected_summary = _job_summary(selected, settings)
        selected_summary["artifacts"] = names
        for name in names[:_SAMPLE_ARTIFACT_LIMIT]:
            try:
                content = artifacts.read(str(selected["id"]), name)["content"]
            except JobServiceError:
                # An artifact listed but unreadable (evicted, binary) must not
                # sink the whole context response; the agent still sees the name.
                continue
            if len(content) > _SAMPLE_MAX_CHARS:
                truncated.append(name)
                content = content[:_SAMPLE_MAX_CHARS]
            samples[name] = content

    return {
        "workspace_id": workspace_id,
        "recent_jobs": [_job_summary(job, settings) for job in jobs],
        "selected_job": selected_summary,
        "samples": samples,
        "sample_max_chars": _SAMPLE_MAX_CHARS,
        "truncated": truncated,
    }
