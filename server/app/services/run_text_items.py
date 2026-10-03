"""``text`` run items → ready materials (materials-and-runs design §4.1).

A text item carries requirement text typed straight into the add-items
dialog. Before resolution it is persisted as a Markdown / plain-text
material — stage the batch's objects, then commit all rows together — and
rewritten to an ordinary ``material`` item, so every downstream stage
(resolution, ``jobs.input_json``, the claim manifest, worker
materialization, the skill) sees exactly what a manual upload of the same
file would produce. Content-addressed sha256 identity means resubmitting
identical text reuses the material and hits the same job dedup key as
re-uploading the same file; a ready row with the same hash is reused
without touching the object store at all. Concurrent losers and failed
batches compensate only their uniquely named objects (run_text_batch).

This is the only write RunService performs before the run row exists; a
material is a workspace asset (TTL-collected when unreferenced), which is
precisely what a manual upload leaves behind when the run is rejected
afterwards, so the fail-closed creation contract is unchanged.

#813: an item's ``client_token`` is captured here, before normalization, and
carried onto the rewritten material item, so identical text under different
tokens still shares one material but resolves to independent jobs
(run_item_client_token). ``.json`` joins the filename allowlist for
hand-typed JSON payloads.
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.services.materials import MaterialsService, MaterialStorageUnavailableError
from server.app.services.run_item_client_token import item_client_token
from server.app.services.run_text_batch import TEXT_CONTENT_TYPES, store_text_batch

TEXT_ITEM_MAX_BYTES = 64 * 1024
DEFAULT_TEXT_FILENAME = "需求.md"


def is_text_item(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") == "text"


def text_item_filename(raw: Any, default: str = "") -> str:
    """The material filename for a text item: a bare ``.md`` / ``.txt`` / ``.json`` name."""
    name = str(raw or "").strip() or default.strip() or DEFAULT_TEXT_FILENAME
    if "/" in name or "\\" in name or name.startswith("."):
        raise InvalidOperationError(f"text item filename is invalid: {name!r}")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise InvalidOperationError("text item filename contains invalid control characters")
    try:
        name.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidOperationError("text item filename must be valid UTF-8") from exc
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    if suffix not in TEXT_CONTENT_TYPES:
        raise InvalidOperationError("text item filename must end with .md, .txt or .json")
    return name


def _prepare(items: list[dict[str, Any]], default_filename: str) -> list[tuple[int, str, bytes]]:
    """Shape-check every text item before anything is written."""
    prepared: list[tuple[int, str, bytes]] = []
    for index, item in enumerate(items):
        if not is_text_item(item):
            continue
        item_client_token(item)
        content = str(item.get("content") or "")
        if not content.strip():
            raise InvalidOperationError("text item requires non-empty content")
        try:
            payload = content.encode("utf-8")
        except UnicodeError as exc:
            raise InvalidOperationError("text item content must be valid UTF-8") from exc
        if len(payload) > TEXT_ITEM_MAX_BYTES:
            raise InvalidOperationError(
                f"text item exceeds {TEXT_ITEM_MAX_BYTES} bytes ({len(payload)} bytes)"
            )
        prepared.append(
            (index, text_item_filename(item.get("filename"), default_filename), payload)
        )
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
    resolved = list(items)
    for index, material_id in store_text_batch(
        job_db, storage, workspace_id, prepared, created_by
    ).items():
        material_item: dict[str, Any] = {"type": "material", "material_id": material_id}
        token = item_client_token(items[index])
        if token:
            material_item["client_token"] = token
        resolved[index] = material_item
    return resolved
