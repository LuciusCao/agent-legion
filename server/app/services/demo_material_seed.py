"""Demo workspace factory seed: examples/ markdown as sample materials (design §9).

The demo workflow's intake node reads its knowledge-point markdown from a
material job input (``ctx.material``), so a fresh demo workspace needs the
repo's ``examples/education-video-problems-generation/*.md`` files uploaded
as ready materials — then a new user can click-run with zero preparation.

Same seed-if-absent discipline as the node code seed
(``demo_node_seed.py``): idempotency key = the materials table's
``(workspace_id, content_hash)`` dedup identity, so reseeding never
duplicates, and an existing row with the same hash (whatever its status or
origin) is authoritative and never overwritten.

Storage is instance infrastructure (env-only ``AGENT_LEGION_S3_*``,
MATERIAL-SECRET-001): an unconfigured instance (no bucket) skips seeding
with a warning and never blocks workspace creation or startup; a configured
but unreachable store degrades the same way (warning, partial seed at most —
the next seed call completes the rest).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import TYPE_CHECKING

from server.app.db.transaction import read_connection, write_transaction
from server.app.storage import ObjectStorage, build_s3_storage

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = logging.getLogger(__name__)

DEMO_MATERIALS_DIR = "examples/education-video-problems-generation"
_CONTENT_TYPE = "text/markdown; charset=utf-8"


def _existing_hashes(settings: Settings, workspace_id: str) -> set[str]:
    with read_connection(settings.database_url) as conn:
        rows = conn.execute(
            "select content_hash from materials where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return {str(row["content_hash"]) for row in rows}


def seed_demo_workspace_materials(
    settings: Settings,
    workspace_id: str,
    *,
    storage: ObjectStorage | None = None,
) -> list[str]:
    """Seed the examples/ knowledge markdown as ready materials; returns filenames seeded.

    ``storage`` is the test seam (a fake ObjectStorage); when omitted the
    instance env resolves it and an unconfigured instance skips seeding.
    """
    if storage is None:
        storage = build_s3_storage()
    if storage is None:
        logger.warning(
            "demo material seed skipped for workspace %s: object storage is not "
            "configured (AGENT_LEGION_S3_BUCKET unset)",
            workspace_id,
        )
        return []
    source_dir = settings.root_dir / DEMO_MATERIALS_DIR
    if not source_dir.is_dir():
        logger.warning(
            "demo material seed skipped for workspace %s: %s not found",
            workspace_id,
            source_dir,
        )
        return []

    seeded: list[str] = []
    known_hashes = _existing_hashes(settings, workspace_id)
    for path in sorted(source_dir.glob("*.md")):
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest in known_hashes:
            continue
        storage_key = f"{workspace_id}/{digest}/{path.name}"
        try:
            # Object first, row second: a ready row always has its object
            # (a crashed seed between the two simply re-puts on the next run —
            # the content-addressed key makes the put idempotent).
            storage.put_object(storage_key, payload, content_type=_CONTENT_TYPE)
            with write_transaction(settings.database_url) as conn:
                conn.execute(
                    """
                    insert into materials(
                      id, workspace_id, content_hash, filename, content_type,
                      size_bytes, storage_key, status, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, 'ready', 'system')
                    on conflict (workspace_id, content_hash)
                    where content_hash <> '' do nothing
                    """,
                    (
                        uuid.uuid4().hex,
                        workspace_id,
                        digest,
                        path.name,
                        _CONTENT_TYPE,
                        len(payload),
                        storage_key,
                    ),
                )
        except Exception:
            logger.warning(
                "demo material seed aborted for workspace %s at %s (object store "
                "unreachable?); seeded so far: %d",
                workspace_id,
                path.name,
                len(seeded),
                exc_info=True,
            )
            break
        known_hashes.add(digest)
        seeded.append(path.name)
        logger.info(
            "seeded demo material: %s -> workspace %s (%d bytes)",
            path.name,
            workspace_id,
            len(payload),
        )
    return seeded
