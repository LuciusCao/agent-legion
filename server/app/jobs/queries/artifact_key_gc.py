"""The job-artifact/material storage-key reference sets used by object GC."""

from __future__ import annotations

from typing import Any


def load_all_object_storage_keys(conn: Any) -> set[str]:
    rows = conn.execute(
        "select storage_key from job_artifacts union select storage_key from materials"
    )
    return {str(row["storage_key"]) for row in rows}


def load_existing_object_storage_keys(conn: Any, storage_keys: list[str]) -> set[str]:
    rows = conn.execute(
        "select storage_key from job_artifacts where storage_key = ANY(%s)"
        " union"
        " select storage_key from materials where storage_key = ANY(%s)",
        (storage_keys, storage_keys),
    )
    return {str(row["storage_key"]) for row in rows}
