from __future__ import annotations

import logging
from pathlib import Path

from server.app.db.connection import DatabaseConnection
from server.app.services.job_run_dir_lookup import (
    build_job_dir_index,
    derive_run_dir_from_index,
)
from server.app.storage_paths import derive_session_dir_from_run_dir, make_data_relative

logger = logging.getLogger(__name__)


def backfill_node_run_dirs(
    conn: DatabaseConnection,
    data_dir: Path,
) -> int:
    """Derive and persist missing run_dir/session_dir for finished node runs."""
    jobs_dir = data_dir / "jobs"
    rows = conn.execute(
        """
        select id, job_id, node_key, log_path, run_dir, session_dir
        from node_runs
        where (run_dir = '' or session_dir = '') and log_path != '' and status in ('completed', 'failed')
        """
    ).fetchall()

    target_job_ids = {str(row["job_id"]) for row in rows}
    job_dir_index = build_job_dir_index(jobs_dir, target_job_ids)
    updated = 0
    for row in rows:
        run_dir = derive_run_dir_from_index(row["job_id"], row["node_key"], job_dir_index)
        if run_dir is None:
            continue
        try:
            new_run_dir = make_data_relative(run_dir, data_dir)
        except Exception as exc:
            logger.warning("Cannot canonicalize run_dir for node_run %s: %s", row["id"], exc)
            continue

        new_session_dir = row["session_dir"]
        if not new_session_dir:
            session_dir = derive_session_dir_from_run_dir(run_dir)
            if session_dir is not None:
                try:
                    new_session_dir = make_data_relative(session_dir, data_dir)
                except Exception as exc:
                    logger.warning(
                        "Cannot canonicalize session_dir for node_run %s: %s", row["id"], exc
                    )

        conn.execute(
            "update node_runs set run_dir = ?, session_dir = ? where id = ?",
            (new_run_dir, new_session_dir, row["id"]),
        )
        updated += 1
    return updated
