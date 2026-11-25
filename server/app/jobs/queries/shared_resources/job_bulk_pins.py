"""Job-id scoping and transactional pin insertion for resource runs."""

from __future__ import annotations

from typing import Any

from server.app.jobs.job_id_encoding import encode_resource_job_id
from server.app.jobs.queries.shared_resources.lifecycle import insert_job_pin_tx
from server.app.workflows.shared_resources import resource_freeze_from_run


def scoped_job_id(workspace_id: str, workflow_key: str, source_id: str, pins_hash: str) -> str:
    return encode_resource_job_id(workspace_id, workflow_key, source_id, pins_hash)


def insert_run_resource_pins(conn: Any, run_id: str, job_ids: list[str]) -> None:
    if not job_ids:
        return
    row = conn.execute("select frozen_pins_json from runs where id=%s", (run_id,)).fetchone()
    freeze = resource_freeze_from_run(row)
    pins = freeze.get("pins")
    if not isinstance(pins, list):
        return
    for job_id in sorted(job_ids):
        for pin in pins:
            insert_job_pin_tx(
                conn,
                job_id=job_id,
                resource_id=str(pin["resource_id"]),
                version_id=str(pin["version_id"]),
                manifest_hash=str(pin["manifest_hash"]),
                grant_id=str(pin["grant_id"]),
            )
