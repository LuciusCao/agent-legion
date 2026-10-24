"""Bounded selection buffering for asynchronous batch operations."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import asdict
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_batch_operations import (
    JOB_BATCH_OPERATION_KINDS,
    KINDS_DEDUP_SELECTION,
)
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_failure_selection import resolve_failed_job_ids_page
from server.app.services.job_selection_resolver import resolve_job_ids_page


def submit_operation(
    job_db: JobQueries,
    workspace_id: str,
    kind: str,
    ids: list[str],
    params: dict[str, Any],
    created_by: str,
    *,
    retry_of: str | None = None,
    stream: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one accepted operation and its frozen target spec."""
    if kind not in JOB_BATCH_OPERATION_KINDS:
        raise ValueError(f"unknown batch operation kind: {kind!r}")
    if kind in KINDS_DEDUP_SELECTION:
        frozen = list(dict.fromkeys(value.strip() for value in ids if value.strip()))
    else:
        frozen = list(ids)
    target_spec: dict[str, Any] = {"job_ids": frozen, "params": params}
    if stream is not None:
        target_spec["stream"] = stream
    if retry_of is not None:
        target_spec["retry_of"] = retry_of
    op_id = uuid.uuid4().hex
    job_db.create_job_batch_operation(
        op_id, workspace_id, kind, target_spec, len(frozen), created_by
    )
    row = job_db.get_job_batch_operation(op_id)
    if row is None:
        raise RuntimeError("job batch operation row missing right after its write")
    return row


def prepare_batch_selection(
    job_db: JobQueries,
    workspace_id: str,
    job_ids: Sequence[str] | None,
    job_filter: JobListFilter | None,
    exclude_ids: Collection[str],
    threshold: int,
) -> tuple[list[str], dict[str, Any] | None]:
    if job_filter is None:
        return list(job_ids or []), None
    return _prepare_filter(job_db, workspace_id, job_filter, exclude_ids, threshold)


def prepare_failure_selection(
    job_db: JobQueries,
    workspace_id: str,
    category: str,
    job_ids: Sequence[str],
    job_filter: JobListFilter | None,
    exclude_ids: Collection[str],
    threshold: int,
) -> tuple[list[str], dict[str, Any] | None]:
    ids = list(job_ids)
    if job_filter is not None:
        ids, stream = _prepare_filter(job_db, workspace_id, job_filter, exclude_ids, threshold)
        if ids:
            return ids, stream
    if ids:
        return ids, None
    return _prepare_failures(job_db, workspace_id, category, threshold)


def _prepare_filter(
    job_db: JobQueries,
    workspace_id: str,
    job_filter: JobListFilter,
    exclude_ids: Collection[str],
    threshold: int,
) -> tuple[list[str], dict[str, Any] | None]:
    ids, cursor, freeze_at = _initial_page(
        lambda limit, cursor, freeze_at: resolve_job_ids_page(
            job_db,
            workspace_id,
            job_filter,
            limit,
            cursor=cursor,
            freeze_at=freeze_at,
            exclude_ids=exclude_ids,
        ),
        threshold,
    )
    if len(ids) <= threshold:
        return ids, None
    stream = {
        "source": "filter",
        "filter": asdict(job_filter),
        "exclude_ids": sorted({value.strip() for value in exclude_ids if value.strip()}),
        "freeze_at": freeze_at,
        "next_cursor": cursor,
        "buffer_start": 0,
    }
    return ids, stream


def _prepare_failures(
    job_db: JobQueries, workspace_id: str, category: str, threshold: int
) -> tuple[list[str], dict[str, Any] | None]:
    ids, cursor, freeze_at = _initial_page(
        lambda limit, cursor, freeze_at: resolve_failed_job_ids_page(
            job_db,
            workspace_id,
            category,
            limit,
            cursor=cursor,
            freeze_at=freeze_at,
        ),
        threshold,
    )
    if len(ids) <= threshold:
        return ids, None
    stream = {
        "source": "failures",
        "category": category,
        "freeze_at": freeze_at,
        "next_cursor": cursor,
        "buffer_start": 0,
    }
    return ids, stream


def _initial_page(page: Any, threshold: int) -> tuple[list[str], str | None, str | None]:
    ids: list[str] = []
    cursor: str | None = None
    freeze_at: str | None = None
    while len(ids) <= threshold and (cursor is not None or not ids):
        page_ids, cursor, freeze_at = page(threshold + 1 - len(ids), cursor, freeze_at)
        ids.extend(page_ids)
        if cursor is None:
            break
    return ids, cursor, freeze_at


def load_chunk(
    job_db: JobQueries,
    workspace_id: str,
    payload: dict[str, Any],
    start: int,
    chunk_size: int,
) -> tuple[list[str], int, int, dict[str, Any], bool]:
    """Return ids, buffer start, discovered total, target spec, spec-changed."""
    stream = payload.get("stream")
    if stream is None:
        ids = payload["job_ids"]
        return ids, 0, len(ids), payload, False
    ids = payload["job_ids"]
    buffer_start = int(stream["buffer_start"])
    cursor = stream["next_cursor"]
    changed = False
    if start == buffer_start + len(ids) and cursor is not None:
        buffer_start = start
        ids, cursor = _next_buffer(job_db, workspace_id, stream, cursor, chunk_size)
        payload = {
            **payload,
            "job_ids": ids,
            "stream": {**stream, "next_cursor": cursor, "buffer_start": buffer_start},
        }
        changed = True
    elif start > buffer_start + len(ids) or start < buffer_start:
        raise ValueError("batch stream cursor is outside its buffered range")
    return ids, buffer_start, buffer_start + len(ids), payload, changed


def _next_buffer(
    job_db: JobQueries,
    workspace_id: str,
    stream: dict[str, Any],
    cursor: str,
    chunk_size: int,
) -> tuple[list[str], str | None]:
    ids: list[str] = []
    next_cursor: str | None = cursor
    while next_cursor is not None and len(ids) < chunk_size:
        limit = chunk_size - len(ids)
        if stream["source"] == "filter":
            page, next_cursor, _ = resolve_job_ids_page(
                job_db,
                workspace_id,
                JobListFilter(**stream["filter"]),
                limit,
                cursor=next_cursor,
                freeze_at=stream["freeze_at"],
                exclude_ids=stream["exclude_ids"],
            )
        else:
            page, next_cursor, _ = resolve_failed_job_ids_page(
                job_db,
                workspace_id,
                str(stream["category"]),
                limit,
                cursor=next_cursor,
                freeze_at=stream["freeze_at"],
            )
        ids.extend(page)
    return ids, next_cursor
