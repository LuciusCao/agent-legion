"""``text`` run items → ready materials (materials-and-runs design §4.1).

A text item carries requirement text typed straight into the add-items
dialog. Before resolution it is persisted as a Markdown / plain-text
material — object first, row second, the demo seed's discipline — and
rewritten to an ordinary ``material`` item, so every downstream stage
(resolution, ``jobs.input_json``, the claim manifest, worker
materialization, the skill) sees exactly what a manual upload of the same
file would produce. Content-addressed sha256 identity means resubmitting
identical text reuses the material and hits the same job dedup key as
re-uploading the same file; a ready row with the same hash is reused
without touching the object store at all (no orphan object under a second
filename).

This is the only write RunService performs before the run row exists; a
material is a workspace asset (TTL-collected when unreferenced), which is
precisely what a manual upload leaves behind when the run is rejected
afterwards, so the fail-closed creation contract is unchanged.
"""

from __future__ import annotations

import hashlib
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.services.job_errors import InvalidOperationError
from server.app.services.material_ttl import materials_ttl_days
from server.app.services.materials import MaterialsService, MaterialStorageUnavailableError

TEXT_ITEM_MAX_BYTES = 64 * 1024
DEFAULT_TEXT_FILENAME = "需求.md"
_CONTENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


def is_text_item(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") == "text"


def text_item_filename(raw: Any, default: str = "") -> str:
    """The material filename for a text item: a bare ``.md`` / ``.txt`` name.

    ``default`` is the start node's ``text_input.filename`` (already validated
    at definition load); the built-in name applies when both are empty.
    """
    name = str(raw or "").strip() or default.strip() or DEFAULT_TEXT_FILENAME
    if "/" in name or "\\" in name or name.startswith("."):
        raise InvalidOperationError(f"text item filename is invalid: {name!r}")
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    if suffix not in _CONTENT_TYPES:
        raise InvalidOperationError("text item filename must end with .md or .txt")
    return name


def _prepare(items: list[dict[str, Any]], default_filename: str) -> list[tuple[int, str, bytes]]:
    """Shape-check every text item before anything is written."""
    prepared: list[tuple[int, str, bytes]] = []
    for index, item in enumerate(items):
        if not is_text_item(item):
            continue
        content = str(item.get("content") or "")
        if not content.strip():
            raise InvalidOperationError("text item requires non-empty content")
        payload = content.encode("utf-8")
        if len(payload) > TEXT_ITEM_MAX_BYTES:
            raise InvalidOperationError(
                f"text item exceeds {TEXT_ITEM_MAX_BYTES} bytes ({len(payload)} bytes)"
            )
        filename = text_item_filename(item.get("filename"), default_filename)
        prepared.append((index, filename, payload))
    return prepared


def materialize_text_items(
    job_db: Any,
    materials: MaterialsService | None,
    workspace_id: str,
    items: list[dict[str, Any]],
    *,
    created_by: str = "",
    default_filename: str = "",
) -> list[dict[str, Any]]:
    """Return ``items`` with every text item replaced by a material item.

    Shape errors (empty content, oversized payload, bad filename) raise
    before anything is written; an unconfigured or unreachable object store
    maps to 503 exactly like the materials API.
    """
    if not any(is_text_item(item) for item in items):
        return items
    storage = materials.storage if materials is not None else None
    if storage is None:
        raise MaterialStorageUnavailableError(
            "Material storage is not configured on this instance "
            "(AGENT_LEGION_S3_BUCKET is unset); text items cannot be stored"
        )
    prepared = _prepare(items, default_filename)
    ttl_days = materials_ttl_days(job_db)
    resolved = list(items)
    for index, filename, payload in prepared:
        digest = hashlib.sha256(payload).hexdigest()
        existing = job_db.find_material_by_hash(workspace_id, digest)
        if existing is not None and existing["status"] == "ready":
            # Same bytes already stored (upload or earlier text item): reuse
            # the row and its object; a second filename would only orphan
            # an object nothing references.
            resolved[index] = {"type": "material", "material_id": existing["id"]}
            continue
        storage_key = f"{workspace_id}/{digest}/{filename}"
        content_type = _CONTENT_TYPES[filename[filename.rfind(".") :].lower()]
        try:
            storage.put_object(storage_key, payload, content_type=content_type)
        except (ClientError, BotoCoreError, OSError) as exc:
            # The boto3 data-plane failure family (same as the seed/TTL
            # sweeps): the store is configured but not usable right now.
            raise MaterialStorageUnavailableError(
                f"Material storage is unreachable; text item could not be stored: {exc}"
            ) from exc
        material_id = job_db.upsert_ready_material(
            workspace_id,
            content_hash=digest,
            filename=filename,
            content_type=content_type,
            size_bytes=len(payload),
            storage_key=storage_key,
            created_by=created_by,
            ttl_days=ttl_days,
        )
        resolved[index] = {"type": "material", "material_id": material_id}
    return resolved
