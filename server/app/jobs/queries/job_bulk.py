from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.job_bulk_sql import (
    BUNDLE_LOCK_IN_SQL,
    CHUNK_ROWS,
    MATERIAL_LOCK_IN_SQL,
    insert_job_nodes_batched,
    insert_jobs_batched,
    job_row_tuple,
    lock_rows_for_key_share,
)
from server.app.jobs.run_freeze import candidate_input
from server.app.jobs.storage_layout import job_storage_dir


def _job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{workflow_key}_{safe_source_id}"


class JobBulkQueriesMixin(ConnectionQueriesMixin):
    jobs_dir: Path

    def fetch_jobs_by_ids(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Full job rows for ``job_ids``, input order (chunked IN reads).

        Legacy job-batches wire shape still materializes rows (#467 A4).
        """
        rows: list[dict[str, Any]] = []
        for start in range(0, len(job_ids), CHUNK_ROWS):
            chunk = job_ids[start : start + CHUNK_ROWS]
            placeholders = ",".join("%s" for _ in chunk)
            with self._connect_read() as conn:
                found = conn.execute(
                    f"select * from jobs where id in ({placeholders})", chunk
                ).fetchall()
            by_id = {str(row["id"]): dict(row) for row in found}
            for job_id in chunk:
                row = by_id.get(job_id)
                if row is not None:
                    rows.append(row)
        return rows

    def create_jobs_bulk(
        self,
        *,
        candidates: list[dict[str, Any]],
        workflow_key: str,
        run_id: str,
        node_keys: list[str],
        workspace_id: str,
        revision: dict[str, Any],
        frozen_config: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Insert one job per candidate of a run, in chunked transactions.

        Each job carries the run's frozen config + its own input doc
        (RUN-FREEZE-001); a re-submitted job takes the new freeze.

        #467 A3 — chunked-commit protocol: ≤1000-row chunks, one transaction
        each; a mid-run failure leaves earlier chunks committed (the run
        service marks the partial run failed, and resubmitting the same
        items resumes through the dedup filter — the intake queue's
        chunk-error contract). Normalize collisions (``a/b`` vs ``a_w``) and
        identity mismatches are detected over the WHOLE candidate set before
        the first chunk commits, so a collision inserts nothing. Returns job
        ids (first-seen order); row materialization moved to read paths (A4).
        """
        if not candidates:
            return []
        frozen_config_json = (
            json.dumps(dict(frozen_config), ensure_ascii=False, sort_keys=True)
            if frozen_config
            else None
        )
        rows: dict[str, tuple[Any, ...]] = {}
        job_ids: list[str] = []
        identities: dict[str, tuple[str, str]] = {}
        material_ids: list[str] = []
        bundle_ids: list[str] = []
        for candidate in candidates:
            source_id = str(candidate["entity_id"])
            job_id = _job_id(workspace_id, workflow_key, source_id)
            identity = (str(candidate["entity_type"]), source_id)
            if job_id in identities and identities[job_id] != identity:
                raise ValueError(f"Job identity collision for {job_id}")
            identities[job_id] = identity
            input_doc = candidate_input(candidate)
            if input_doc.get("type") == "material":
                material_ids.append(str(input_doc.get("material_id") or ""))
            elif input_doc.get("type") == "bundle":
                bundle_ids.append(str(input_doc.get("bundle_id") or ""))
            # dict-keyed insert = dedup by job id, last row wins (the
            # executemany shape's later-DO-UPDATE-wins semantics — see
            # job_bulk_sql's JOBS_BULK_INSERT_SQL comment).
            if job_id not in rows:
                job_ids.append(job_id)
            rows[job_id] = job_row_tuple(
                self.jobs_dir,
                workspace_id,
                workflow_key,
                run_id,
                revision,
                candidate,
                source_id,
                job_id,
                frozen_config_json,
            )
        row_list = list(rows.values())

        with self.connect() as conn:
            # Material inputs FOR KEY SHARE their materials row: a concurrent
            # material delete (FOR UPDATE) either blocks until these jobs
            # commit (its reference check then rejects with 409) or commits
            # first and the row is gone here. #467 A3: the lock covers the
            # WHOLE call up front, not per chunk (lock_rows_for_key_share);
            # a missing row fails before the first chunk (400 + compensation).
            lock_rows_for_key_share(
                conn,
                MATERIAL_LOCK_IN_SQL,
                list(dict.fromkeys(material_ids)),
                workspace_id,
                kind="Material",
            )
            # Bundle rows lock the same way against the bundle delete guard (#156).
            lock_rows_for_key_share(
                conn,
                BUNDLE_LOCK_IN_SQL,
                list(dict.fromkeys(bundle_ids)),
                workspace_id,
                kind="Material bundle",
            )
            placeholders = ",".join("%s" for _ in job_ids)
            existing = conn.execute(
                # identity columns only — select * would drag
                # workflow_definition_snapshot_json through for every row
                "select id, workspace_id, source_type, source_id from jobs"
                f" where id in ({placeholders})",
                job_ids,
            ).fetchall()
            by_id = {str(row["id"]): row for row in existing}
            for row in row_list:
                current = by_id.get(str(row[0]))
                if current is not None and (
                    current["workspace_id"] != row[1]
                    or current["source_type"] != row[2]
                    or current["source_id"] != row[3]
                ):
                    raise ValueError(f"Job identity collision for {row[0]}")
            # One commit per ≤CHUNK_ROWS jobs+nodes: a mid-run failure leaves
            # whole chunks behind (resumable via dedup — docstring), and the
            # lock window per transaction is bounded by one chunk. Storage
            # dirs are created per chunk AFTER the existing-row check: for a
            # resubmitted job the on-conflict update keeps the stored
            # storage_dir, and a stray shard dir would block the one-shot
            # flat→sharded migration as a conflict.
            for start in range(0, len(row_list), CHUNK_ROWS):
                chunk = row_list[start : start + CHUNK_ROWS]
                for row in chunk:
                    if str(row[0]) not in by_id:
                        job_storage_dir(self.jobs_dir, workspace_id, str(row[0])).mkdir(
                            parents=True, exist_ok=True
                        )
                insert_jobs_batched(conn, chunk)
                insert_job_nodes_batched(
                    conn,
                    [(str(row[0]), node_key) for row in chunk for node_key in node_keys],
                )
                conn.commit()
        return job_ids
