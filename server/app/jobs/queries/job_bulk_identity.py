"""Post-INSERT identity verification for chunked job bulk (#501, PR #497).

Split out of ``job_bulk_rows.py`` for the file-size budget (same precedent
as ``job_bulk_sql.py``). Owns the write-side half of the identity contract:
the precheck in ``job_bulk_rows.fetch_identity_map`` is a fast-path read
that cannot see concurrent in-flight same-id inserts, so every chunk's
transaction re-verifies AFTER its INSERT settles.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.job_bulk_rows import fetch_identity_map


def identity_mismatch_error(job_id: str, current: Any, expected: tuple[Any, ...]) -> ValueError:
    """Structured identity-collision error for ``job_id`` against an existing row.

    #501: the bare message hid which identity lost; the attributes let
    callers (and logs) see the surviving row's (source_type, source_id)
    against the submitted one — the same tuple shape the checks compare.
    """
    error = ValueError(
        f"Job identity collision for {job_id}:"
        f" existing source ({current['workspace_id']}, {current['source_type']},"
        f" {current['source_id']})"
        f" vs submitted ({expected[0]}, {expected[1]}, {expected[2]})"
    )
    error.job_id = job_id  # type: ignore[attr-defined]
    error.existing_workspace_id = current["workspace_id"]  # type: ignore[attr-defined]
    error.existing_source_type = current["source_type"]  # type: ignore[attr-defined]
    error.existing_source_id = current["source_id"]  # type: ignore[attr-defined]
    error.submitted_source_type = expected[1]  # type: ignore[attr-defined]
    error.submitted_source_id = expected[2]  # type: ignore[attr-defined]
    return error


def verify_chunk_identities(conn: Any, chunk: list[tuple[Any, ...]]) -> None:
    """Post-INSERT identity verification for one committed-chunk-to-be (#501).

    Runs inside the chunk's own transaction, AFTER ``insert_jobs_batched``
    has settled (own inserts visible; a concurrent same-id INSERT has been
    arbitrated by the unique index — this side either waited and rebinded, or
    won and the other side's rebind waits on this transaction). The ON
    CONFLICT arm rebinds run/title/input but deliberately does NOT touch
    source_type/source_id, so a row whose identity differs from this
    submission's proves another identity won the id — raise (the chunk rolls
    back whole) instead of silently leaving the foreign row re-bound to this
    run. Closes the precheck's read-then-insert window: a concurrent submit
    can slip an insert past the precheck (its row was in flight at read
    time), but it cannot slip one past this check.
    """
    by_id = fetch_identity_map(conn, [str(row[0]) for row in chunk])
    for row in chunk:
        current = by_id.get(str(row[0]))
        if current is not None and (
            current["workspace_id"] != row[1]
            or current["source_type"] != row[2]
            or current["source_id"] != row[3]
        ):
            raise identity_mismatch_error(str(row[0]), current, (row[1], row[2], row[3]))
