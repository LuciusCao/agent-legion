"""Exact historical pins for API detail and Host materialization."""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.shared_resources.run_accept import (
    SharedResourceRunQueriesMixin,
    _source,
)


def job_resource_pins_tx(conn: Any, job_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select p.resource_id, p.version_id, p.manifest_hash, p.grant_id,"
        " r.name, v.version, v.source_type, v.source_id, v.source_workspace_id,"
        " v.source_snapshot_json from shared_resource_job_pins p"
        " join shared_resource_versions v on v.id=p.version_id"
        " join shared_resources r on r.id=p.resource_id"
        " where p.job_id=%s order by p.resource_id",
        (job_id,),
    ).fetchall()
    pins: list[dict[str, Any]] = []
    for row in rows:
        files = conn.execute(
            "select path, sha256, size_bytes, content_type, storage_key"
            " from shared_resource_files where version_id=%s order by ordinal",
            (row["version_id"],),
        ).fetchall()
        pins.append(
            {
                "resource_id": str(row["resource_id"]),
                "name": str(row["name"]),
                "version_id": str(row["version_id"]),
                "version": int(row["version"]),
                "manifest_hash": str(row["manifest_hash"]),
                "grant_id": str(row["grant_id"]),
                "source": _source(row),
                "files": [
                    {
                        "path": str(file["path"]),
                        "sha256": str(file["sha256"]),
                        "size_bytes": int(file["size_bytes"]),
                        "content_type": str(file["content_type"]),
                        "storage_key": str(file["storage_key"]),
                    }
                    for file in files
                ],
            }
        )
    return pins


class SharedResourceJobPinQueriesMixin(SharedResourceRunQueriesMixin):
    def get_job_shared_resource_pins(self, job_id: str) -> list[dict[str, Any]]:
        """Return exact pinned descriptors, including revoked/deleted history."""
        with self.read() as conn:
            return job_resource_pins_tx(conn, job_id)
