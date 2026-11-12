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
from typing import TYPE_CHECKING

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection
from server.app.services.job_errors import ConflictError
from server.app.services.material_object_publish import (
    MaterialObjectWrite,
    publish_material_objects,
)
from server.app.storage import ObjectStorage, build_s3_storage

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = logging.getLogger(__name__)

DEMO_MATERIALS_DIR = "examples/education-video-problems-generation"
_CONTENT_TYPE = "text/markdown; charset=utf-8"


def _existing_hashes(connect_source: ConnectSource, workspace_id: str) -> set[str]:
    with read_connection(connect_source) as conn:
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
    connect_source: ConnectSource | None = None,
) -> list[str]:
    """Seed the examples/ knowledge markdown as ready materials; returns filenames seeded.

    ``storage`` is the test seam (a fake ObjectStorage); when omitted the
    instance env resolves it and an unconfigured instance skips seeding.
    ``connect_source`` is the JobQueries facade (BOUNDARY-DATA-001, #187);
    None falls back to the settings DSN (tests, scripts).
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
    source = connect_source if connect_source is not None else settings.database_url
    known_hashes = _existing_hashes(source, workspace_id)
    for path in sorted(source_dir.glob("*.md")):
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest in known_hashes:
            continue
        # 服务端材料写入 chokepoint（#1314）：先落 uploading intent 行、
        # 再 PUT、再原子换成 ready 行——GC 反查在任何时刻都见行引用；
        # #1279 key 文法（v2g0 + m_{material_id} 唯一段，key 尾段为文件
        # 名）由 chokepoint 派生，删行后重播种子也必得新 key。同 hash
        # 已有行（任意状态/来源）时不覆盖（on_non_ready='skip'）。Demo
        # 材料不设 TTL（ttl_days=0，expires_at 为 NULL）。
        write = MaterialObjectWrite(
            content_hash=digest,
            filename=path.name,
            content_type=_CONTENT_TYPE,
            payload=payload,
            key_tail=path.name,
        )
        try:
            published = publish_material_objects(
                source,
                storage,
                workspace_id,
                [write],
                created_by="system",
                ttl_days=0,
                on_non_ready="skip",
            )
        except (ClientError, BotoCoreError, OSError, psycopg.Error, ConflictError):
            # #204: the seeding loop's failure space is the boto3 data plane
            # (object put), the DB write (psycopg.Error — DB writes raise it, not OSError, codex on #264), and the
            # read of the source markdown (OSError); ConflictError is the
            # chokepoint's "intent row changed under us" (#1314 — e.g. the
            # uploading row was deleted mid-seed). All are transient
            # infrastructure failures the module docstring already frames as
            # "degrade with a warning, the next seed call completes the
            # rest"; an abort (not skip) is correct because the loop's
            # idempotency key is per-file, so a partial seed resumes cleanly.
            # Programming errors propagate to the workspace-creation caller.
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
        if published.get(digest) is None:
            continue
        seeded.append(path.name)
        logger.info(
            "seeded demo material: %s -> workspace %s (%d bytes)",
            path.name,
            workspace_id,
            len(payload),
        )
    return seeded
