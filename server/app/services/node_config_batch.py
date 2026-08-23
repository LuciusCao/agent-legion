"""Frozen node config readers (RUN-FREEZE-001).

Split from ``node_config`` for the file-size budget. Since schema v53 the
authoritative freeze lives on the run/job columns (``runs.frozen_pins_json``,
``jobs.frozen_config_json``); the "payload" these helpers decode is the
equivalent dict rebuilt from those columns by
``server.app.services.run_payload``, never a stored batch payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.services.run_payload import reconstruct_batch_payload


def frozen_node_config(
    batch_payload: Mapping[str, Any] | None,
    node_key: str,
) -> dict[str, Any] | None:
    """Read one node's frozen config from a reconstructed run payload."""
    if not isinstance(batch_payload, Mapping):
        return None
    node_config = batch_payload.get("node_config")
    if not isinstance(node_config, Mapping):
        return None
    values = node_config.get(node_key)
    return dict(values) if isinstance(values, Mapping) else None


def run_frozen_payload(job_db: Any, job: Mapping[str, Any]) -> dict[str, Any] | None:
    """The job's frozen run payload, rebuilt from the run + job columns."""
    run_id = job.get("run_id")
    if not run_id:
        return None
    run = job_db.get_run(str(run_id))
    if run is None:
        return None
    return reconstruct_batch_payload(run, job)
