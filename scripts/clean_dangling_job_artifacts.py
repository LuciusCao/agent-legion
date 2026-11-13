"""Dry-run and explicitly reviewed dangling job-artifact recovery (#1019).

Dry-run always uses server-read-only transactions. Apply additionally requires
schema v2 calibrated plan identity plus operator-pinned bucket and endpoint.
Reports contain deployment identifiers and object keys; keep them local.
"""

from server.app.services.job_rerun.recovery_cli import _parser, main

__all__ = ["_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
