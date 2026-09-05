from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.job_bulk_rows import (
    chunk_ref_ids,
    fetch_identity_map,
    job_row_tuple,
)
from server.app.jobs.queries.job_bulk_sql import (
    BUNDLE_LOCK_IN_SQL,
    CHUNK_ROWS,
    MATERIAL_LOCK_IN_SQL,
    insert_job_nodes_batched,
    insert_jobs_batched,
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
        """Full job rows for ``job_ids``, input order (chunked IN reads;
        the legacy job-batches wire shape still materializes rows)."""
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

        #467 A3 — chunked-commit protocol: ≤CHUNK_ROWS-row chunks, one
        transaction each. Every chunk FOR KEY SHARE-locks exactly its own
        material/bundle refs before inserting (per-chunk lock-before-insert,
        review P1-1; the delete-serialization argument lives on
        ``job_bulk_sql.lock_rows_for_key_share``). A mid-run failure leaves
        earlier chunks committed: the run service marks the partial run
        failed (operator legibility) and a resubmission of the same items
        resumes through the dedup filter — the async intake queue's
        chunk-error contract. Normalize collisions (``a/b`` vs ``a_w``) and
        identity mismatches are detected over the WHOLE candidate set before
        the first chunk commits (chunked identity precheck, review P2-2), so
        a collision inserts nothing. Returns job ids (first-seen order); row
        materialization moved to read paths (#467 A4).
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
        job_refs: dict[str, tuple[str, str]] = {}
        for candidate in candidates:
            source_id = str(candidate["entity_id"])
            job_id = _job_id(workspace_id, workflow_key, source_id)
            identity = (str(candidate["entity_type"]), source_id)
            if job_id in identities and identities[job_id] != identity:
                raise ValueError(f"Job identity collision for {job_id}")
            identities[job_id] = identity
            input_doc = candidate_input(candidate)
            input_type = str(input_doc.get("type") or "")
            if input_type in ("material", "bundle"):
                job_refs[job_id] = (input_type, str(input_doc.get(f"{input_type}_id") or ""))
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
            # Identity precheck over the WHOLE call, in chunked statements:
            # a collision against an existing row rejects the request before
            # the first chunk commits (nothing inserted).
            by_id = fetch_identity_map(conn, job_ids)
            for row in row_list:
                current = by_id.get(str(row[0]))
                if current is not None and (
                    current["workspace_id"] != row[1]
                    or current["source_type"] != row[2]
                    or current["source_id"] != row[3]
                ):
                    raise ValueError(f"Job identity collision for {row[0]}")
            # One commit per ≤CHUNK_ROWS jobs+nodes; each chunk locks its own
            # refs FOR KEY SHARE before its INSERT (P1-1) — a between-chunks
            # delete makes the next chunk's probe fail on the missing row
            # instead of letting a dangling reference in. Storage dirs are
            # created AFTER the precheck (stray shard dirs would block the
            # flat→sharded migration; resubmits keep the stored dir).
            for start in range(0, len(row_list), CHUNK_ROWS):
                chunk = row_list[start : start + CHUNK_ROWS]
                lock_rows_for_key_share(
                    conn,
                    MATERIAL_LOCK_IN_SQL,
                    chunk_ref_ids(job_refs, chunk, "material"),
                    workspace_id,
                    kind="Material",
                )
                lock_rows_for_key_share(
                    conn,
                    BUNDLE_LOCK_IN_SQL,
                    chunk_ref_ids(job_refs, chunk, "bundle"),
                    workspace_id,
                    kind="Material bundle",
                )
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
