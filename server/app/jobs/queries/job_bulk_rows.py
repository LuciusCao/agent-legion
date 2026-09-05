"""Row/identity helpers for chunked job bulk inserts (#467 review P1-1/P2-2).

Split out of ``job_bulk_sql.py`` for the file-size budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.jobs.queries.job_bulk_sql import CHUNK_ROWS
from server.app.jobs.run_freeze import candidate_input
from server.app.jobs.storage_layout import job_storage_dir
from server.app.storage_paths import make_data_relative

_IDENTITY_SQL = "select id, workspace_id, source_type, source_id from jobs where id in ({ids})"


def fetch_identity_map(conn: Any, job_ids: list[str]) -> dict[str, Any]:
    """``job_id → identity row`` for the ids that exist, in chunked IN reads."""
    by_id: dict[str, Any] = {}
    for start in range(0, len(job_ids), CHUNK_ROWS):
        chunk = job_ids[start : start + CHUNK_ROWS]
        placeholders = ",".join("%s" for _ in chunk)
        rows = conn.execute(_IDENTITY_SQL.format(ids=placeholders), chunk).fetchall()
        by_id.update({str(row["id"]): row for row in rows})
    return by_id


def chunk_ref_ids(
    job_refs: dict[str, tuple[str, str]], chunk: list[tuple[Any, ...]], kind: str
) -> list[str]:
    """Distinct material/bundle ids referenced by one chunk's rows — the id
    list of that chunk's own FOR KEY SHARE probe."""
    return list(
        dict.fromkeys(
            ref[1]
            for row in chunk
            if (ref := job_refs.get(str(row[0]))) is not None and ref[0] == kind
        )
    )


def job_row_tuple(
    jobs_dir: Path,
    workspace_id: str,
    workflow_key: str,
    run_id: str,
    revision: dict[str, Any],
    candidate: dict[str, Any],
    source_id: str,
    job_id: str,
    frozen_config_json: str | None,
) -> tuple[Any, ...]:
    """The jobs-row insert tuple for one candidate (column order = the
    unnest INSERT's column list)."""
    return (
        job_id,
        workspace_id,
        str(candidate["entity_type"]),
        source_id,
        run_id,
        str(candidate["title"]),
        make_data_relative(job_storage_dir(jobs_dir, workspace_id, job_id), jobs_dir.parent),
        str(candidate.get("stem", "")),
        revision["id"],
        int(revision["version"]),
        revision["definition_hash"],
        revision["definition_json"],
        json.dumps(candidate_input(candidate), ensure_ascii=False),
        frozen_config_json,
    )
