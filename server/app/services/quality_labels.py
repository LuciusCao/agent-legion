"""Quality labels: insert-only verdicts on sampled runs, latest-wins reads.

Labels are never updated or deleted; the newest row per (item_id, target) is
the current label — replay labels further group by replay_id (schema v29).
The reason-code vocabulary lives here (service layer) so
services never import route modules; ``routes/quality_contracts.py``
re-exports it for contract-layer validation.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore
from server.app.services.job_errors import InvalidOperationError, NotFoundError

QUALITY_REASON_CODES = (
    "fact_error",
    "answer_leak",
    "inconsistent_answer",
    "non_conceptual_basis",
    "format_violation",
    "other",
)

# Artifact bodies are inlined into the item detail response; cap each blob so
# a pathological output cannot blow up the payload.
_ARTIFACT_CONTENT_LIMIT = 32 * 1024


def artifact_contents(
    artifact_store: ArtifactStore | None, job_id: str, node_keys: str | set[str]
) -> list[dict[str, Any]]:
    """Inline the stored artifact bodies of one job's node(s) (shared by
    quality item details and replay details)."""
    if artifact_store is None:
        return []
    wanted = {node_keys} if isinstance(node_keys, str) else set(node_keys)
    contents: list[dict[str, Any]] = []
    for ref in artifact_store.refs_for_job(job_id):
        if ref["node_key"] not in wanted:
            continue
        try:
            path = artifact_store.open(ref["hash"])
            raw = path.read_bytes()
        except (ArtifactNotFoundError, OSError):
            continue
        contents.append(
            {
                "name": ref["name"],
                "content": raw[:_ARTIFACT_CONTENT_LIMIT].decode("utf-8", errors="replace"),
                "truncated": len(raw) > _ARTIFACT_CONTENT_LIMIT,
            }
        )
    return contents


_LATEST_RUN_LABEL = """
left join lateral (
  select l.id, l.target, l.verdict, l.reason_codes, l.note, l.labeled_by, l.created_at
  from quality_labels l
  where l.item_id = i.id and l.target = 'run'
  order by l.created_at desc, l.id desc
  limit 1
) lab on true
"""

_ITEM_COLUMNS = """
  i.id, i.batch_id, i.node_run_id, i.job_id, i.node_key, i.capability,
  i.skill_version, i.agent_definition_hash, i.agent_version, i.provider,
  i.model, i.run_status, i.failure_category, i.failure_detail, i.created_at
"""


class QualityLabelService:
    def __init__(self, db_path: DatabaseDsn, artifact_store: ArtifactStore | None = None) -> None:
        self.db_path = db_path
        self.artifact_store = artifact_store

    def _get_item(self, conn, workspace_id: str, item_id: str) -> dict[str, Any]:
        row = conn.execute(
            f"""
            select {_ITEM_COLUMNS}
            from quality_sample_items i
            join quality_sample_batches b on b.id = i.batch_id
            where i.id = %s and b.workspace_id = %s
            """,
            (item_id, workspace_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Sample item not found")
        return dict(row)

    def add_label(
        self,
        workspace_id: str,
        item_id: str,
        *,
        verdict: str,
        reason_codes: list[str] | None = None,
        note: str = "",
        labeled_by: str = "",
        target: str = "run",
        replay_id: str | None = None,
    ) -> dict[str, Any]:
        codes = list(reason_codes or [])
        unknown = sorted(set(codes) - set(QUALITY_REASON_CODES))
        if unknown:
            raise InvalidOperationError(f"Unknown reason codes: {', '.join(unknown)}")
        if replay_id is not None and target != "replay":
            raise InvalidOperationError("replay_id labels must use target 'replay'")
        if target == "replay" and replay_id is None:
            raise InvalidOperationError("replay labels require a replay_id")
        label_id = uuid.uuid4().hex
        with write_transaction(self.db_path) as conn:
            self._get_item(conn, workspace_id, item_id)
            if replay_id is not None:
                replay = conn.execute(
                    "select 1 from quality_replays where id = %s and item_id = %s",
                    (replay_id, item_id),
                ).fetchone()
                if replay is None:
                    raise NotFoundError("Replay not found for this sample item")
            conn.execute(
                """
                insert into quality_labels(
                  id, item_id, target, verdict, reason_codes, note, labeled_by, replay_id
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (label_id, item_id, target, verdict, Jsonb(codes), note, labeled_by, replay_id),
            )
            row = conn.execute("select * from quality_labels where id = %s", (label_id,)).fetchone()
        return dict(row) if row is not None else {}

    def list_batch_items(
        self,
        workspace_id: str,
        batch_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Items of a batch, each with its current (latest 'run'-target) label."""
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        with read_connection(self.db_path) as conn:
            batch = conn.execute(
                "select id from quality_sample_batches where id = %s and workspace_id = %s",
                (batch_id, workspace_id),
            ).fetchone()
            if batch is None:
                raise NotFoundError("Sample batch not found")
            total_row = conn.execute(
                "select count(*) as cnt from quality_sample_items where batch_id = %s",
                (batch_id,),
            ).fetchone()
            rows = conn.execute(
                f"""
                select {_ITEM_COLUMNS},
                  lab.id as label_id, lab.target as label_target,
                  lab.verdict as label_verdict, lab.reason_codes as label_reason_codes,
                  lab.note as label_note, lab.labeled_by as label_labeled_by,
                  lab.created_at as label_created_at
                from quality_sample_items i
                {_LATEST_RUN_LABEL}
                where i.batch_id = %s
                order by i.created_at, i.id
                limit %s offset %s
                """,
                (batch_id, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = {
                key: row[key]
                for key in (
                    "id",
                    "batch_id",
                    "node_run_id",
                    "job_id",
                    "node_key",
                    "capability",
                    "skill_version",
                    "agent_definition_hash",
                    "agent_version",
                    "provider",
                    "model",
                    "run_status",
                    "failure_category",
                    "failure_detail",
                    "created_at",
                )
            }
            if row["label_id"] is not None:
                item["current_label"] = {
                    "id": row["label_id"],
                    "item_id": row["id"],
                    "target": row["label_target"],
                    "verdict": row["label_verdict"],
                    "reason_codes": row["label_reason_codes"],
                    "note": row["label_note"],
                    "labeled_by": row["label_labeled_by"],
                    "created_at": row["label_created_at"],
                }
            else:
                item["current_label"] = None
            items.append(item)
        total = int(total_row["cnt"]) if total_row is not None else 0
        return {"items": items, "total": total}

    def get_item_detail(self, workspace_id: str, item_id: str) -> dict[str, Any]:
        """Snapshot plus full label history and node output artifact contents."""
        with read_connection(self.db_path) as conn:
            item = self._get_item(conn, workspace_id, item_id)
            rows = conn.execute(
                """
                select * from quality_labels
                where item_id = %s
                order by created_at desc, id desc
                """,
                (item_id,),
            ).fetchall()
        return {
            "item": item,
            "labels": [dict(row) for row in rows],
            "artifacts": self._artifact_contents(item["job_id"], item["node_key"]),
        }

    def _artifact_contents(self, job_id: str, node_key: str) -> list[dict[str, Any]]:
        return artifact_contents(self.artifact_store, job_id, node_key)
