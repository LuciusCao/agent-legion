#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the project root importable when the script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.db import Database
from server.app.services.job_run_dir_backfill import backfill_node_run_dirs
from server.app.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    settings = load_settings()
    db = Database(settings.database_url)
    with db.connect() as conn:
        updated = backfill_node_run_dirs(conn, settings.data_dir)
    logger.info("Backfilled %s node run records", updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
