from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.job_bulk_identity import verify_chunk_identities
from server.app.jobs.queries.job_bulk_rows import (
    chunk_ref_ids,
    fetch_identity_map,
    job_row_tuple,
)
from server.app.jobs.queries.job_bulk_sql import (
    BUNDLE_LOCK_IN_SQL,
    CHUNK_ROWS,
    CREATE_CHUNK_ROWS,
    MATERIAL_LOCK_IN_SQL,
    insert_job_nodes_batched,
    insert_jobs_batched,
    lock_job_mutations_sorted,
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
        (RUN-FREEZE-001); a same-run re-submitted job takes the new freeze.

        #467 A3 — chunked-commit protocol: ≤CREATE_CHUNK_ROWS-row chunks, one
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
        a collision inserts nothing. Returns the job ids this call actually
        OWNS after the writes (first-seen order), derived from the INSERT's
        RETURNING (#735 review P1): a candidate whose job a concurrent run
        claimed between the caller's dedup probe and this insert is skipped
        by the ON CONFLICT ownership clause (first writer wins — see
        job_bulk_sql.JOBS_BULK_INSERT_SQL) and drops out of the result, so
        callers never report another run's job as their own. Row
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
            # job_bulk_sql's JOBS_BULK_INSERT_SQL comment; dict order keeps
            # first-seen order).
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
        owned_ids: list[str] = []

        with self.connect() as conn:
            # One commit per ≤CREATE_CHUNK_ROWS jobs+nodes (sized for the
            # shared lock table, see job_bulk_sql). #1083: each chunk takes its
            # ids' job-mutation locks (sorted, one statement, held to the
            # chunk's COMMIT) — creation joins the EXEC-GENERATION-001 lock
            # domain, so a deleted same-id job's post-commit cleanup ("row
            # absent under the lock → rename into .trash") can never move a
            # directory this chunk is creating or adopting. Lock order: the
            # job-mutation level precedes every row lock below. Then the chunk
            # locks its own refs FOR KEY SHARE before its INSERT (P1-1) — a
            # between-chunks delete makes the next chunk's probe fail on the
            # missing row instead of letting a dangling reference in.
            for start in range(0, len(row_list), CREATE_CHUNK_ROWS):
                chunk = row_list[start : start + CREATE_CHUNK_ROWS]
                chunk_ids = [str(row[0]) for row in chunk]
                # #1243 codex P2: ids are concatenated from underscore-bearing
                # fields, so a chunk id can collide with another workspace's
                # committed job (ws ``a`` + source ``a_a_z`` == ws ``a_a`` +
                # source ``z``). Batches order such a job by ITS workspace key,
                # not ours, so locking it in plain job_id order could AB-BA.
                # Reject foreign identities BEFORE taking any lock (plain read,
                # no row locks — same structured error the post-INSERT check
                # raises); with every existing row in our workspace, job_id
                # order IS the global batch order. The post-INSERT re-check
                # below still closes the read-then-insert window (#501).
                verify_chunk_identities(conn, chunk)
                lock_job_mutations_sorted(conn, chunk_ids)
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
                # Storage dirs only for ids with no row yet (stray shard dirs
                # would block the flat→sharded migration; resubmits keep the
                # stored dir). Read under the job-mutation locks, existence
                # is settled against deletion: a row deleted after an
                # unlocked precheck would otherwise get no dir. Not the
                # identity check — #501 keeps that on the write side
                # (verify_chunk_identities after the INSERT settles).
                existing = fetch_identity_map(conn, chunk_ids)
                for job_id in chunk_ids:
                    if job_id not in existing:
                        job_storage_dir(self.jobs_dir, workspace_id, job_id).mkdir(
                            parents=True, exist_ok=True
                        )
                owned_chunk = insert_jobs_batched(conn, chunk)
                # Node rows only for jobs this chunk owns: a skipped row
                # (lost to a concurrent run, #735) belongs to another run —
                # possibly created from another revision — and must not gain
                # this run's node keys.
                insert_job_nodes_batched(
                    conn,
                    [
                        (str(row[0]), node_key)
                        for row in chunk
                        if str(row[0]) in owned_chunk
                        for node_key in node_keys
                    ],
                )
                # #501: post-INSERT identity verification inside this chunk's
                # transaction — the ON CONFLICT arm rebinds run/title/input
                # (same-run rows only, #735) but never touches source
                # identity, so a foreign-identity row that slipped past the
                # precheck (concurrent in-flight insert) shows up here and
                # rolls the whole chunk back instead of being silently
                # re-bound to this run.
                verify_chunk_identities(conn, chunk)
                conn.commit()
                owned_ids.extend(str(row[0]) for row in chunk if str(row[0]) in owned_chunk)
        return owned_ids
