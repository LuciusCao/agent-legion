"""Chunked existence probes for run-item resolution (#467 A1).

Run creation resolves 万级-item submissions; the old shape ran one SELECT per
material/bundle item. These probes are set-based IN queries per chunk, behind
the JobQueries facade (BOUNDARY-DATA-001). The ref/connection probe lives in
``external_connections.py`` (same domain as its single-key sibling).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, TypeVar

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# One IN probe per ≤500 ids — the same bound as the intake resolver chunk
# (job_intake_chunks.INTAKE_RESOLUTION_CHUNK_SIZE).
_PROBE_CHUNK = 500

_MATERIALS_SQL = (
    "select id, status, filename from materials where workspace_id=%s and id in ({ids})"
)
_BUNDLES_SQL = (
    "select b.id, b.name, b.file_count,"
    " (select count(*) from material_bundle_members m"
    "  join materials mat on mat.id = m.material_id"
    "  where m.bundle_id = b.id and mat.status = 'ready') as ready_count"
    " from material_bundles b where b.workspace_id=%s and b.id in ({ids})"
)

# Key/enabled only — no config material (same column whitelist as the
# single-key sibling in external_connections.py).
_CONNECTIONS_SQL = "select key, enabled from external_connections where key in ({keys})"

# #467 A2: dedup point lookups over the request's own keys — each IN chunk
# probes idx_jobs_workspace_source instead of scanning the workspace.
_DEDUP_SQL = (
    "select source_type, source_id from jobs"
    " where workspace_id=%s and (source_type, source_id) in ({pairs})"
)


_T = TypeVar("_T")


def _chunked(items: list[_T]) -> Iterator[list[_T]]:
    for start in range(0, len(items), _PROBE_CHUNK):
        yield items[start : start + _PROBE_CHUNK]


class RunItemProbeQueriesMixin(ConnectionQueriesMixin):
    def fetch_materials_by_ids(
        self, workspace_id: str, material_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """``material_id → row`` (id/status/filename) for the ids found."""
        by_id: dict[str, dict[str, Any]] = {}
        with self._connect_read() as conn:
            for chunk in _chunked(material_ids):
                placeholders = ",".join("%s" for _ in chunk)
                rows = conn.execute(
                    _MATERIALS_SQL.format(ids=placeholders), [workspace_id, *chunk]
                ).fetchall()
                for row in rows:
                    by_id[str(row["id"])] = dict(row)
        return by_id

    def fetch_bundles_by_ids(
        self, workspace_id: str, bundle_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """``bundle_id → row`` (id/name/file_count/ready_count) for the ids found."""
        by_id: dict[str, dict[str, Any]] = {}
        with self._connect_read() as conn:
            for chunk in _chunked(bundle_ids):
                placeholders = ",".join("%s" for _ in chunk)
                rows = conn.execute(
                    _BUNDLES_SQL.format(ids=placeholders), [workspace_id, *chunk]
                ).fetchall()
                for row in rows:
                    by_id[str(row["id"])] = dict(row)
        return by_id

    def external_connection_enabled_map(self, keys: list[str]) -> dict[str, bool | None]:
        """``key → enabled`` for many keys in one chunked IN probe (#467 A1).

        Unknown keys are absent from the map (same unknown-vs-disabled split
        as the single-key read in external_connections).
        """
        result: dict[str, bool | None] = {}
        with self._connect_read() as conn:
            for chunk in _chunked(keys):
                placeholders = ",".join("%s" for _ in chunk)
                rows = conn.execute(_CONNECTIONS_SQL.format(keys=placeholders), chunk).fetchall()
                result.update({str(row["key"]): bool(row["enabled"]) for row in rows})
        return result

    def filter_existing_dedup_keys(
        self, workspace_id: str, keys: Iterable[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        """Subset of ``keys`` present as this workspace's jobs (#467 A2).

        Same workspace-scoped dedup semantics as
        ``list_job_dedup_keys`` (job_keys), but the chunked IN probe hits
        the index: cost tracks the submission size, not the workspace's job
        count. Duplicate keys in ``keys`` are harmless.
        """
        pending = list(dict.fromkeys(keys))
        existing: set[tuple[str, str]] = set()
        with self._connect_read() as conn:
            for chunk in _chunked(pending):
                placeholders = ",".join("(%s,%s)" for _ in chunk)
                params: list[str] = [workspace_id]
                for pair in chunk:
                    params.extend(pair)
                rows = conn.execute(_DEDUP_SQL.format(pairs=placeholders), params).fetchall()
                existing.update((str(r["source_type"]), str(r["source_id"])) for r in rows)
        return existing
