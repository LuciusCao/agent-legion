"""Sandboxed custom code node child entry (EXEC-CODE-003).

Spawned as ``python -m server.app.executors._code_child <result>`` inside
the velites ``sandbox wrap`` confinement: custom node code is user-supplied
(EXEC-CODE-002) and must never run with the server process's full privileges.
The payload pickle arrives on **stdin** (job, job_dir, runtime) so resolved
secrets never touch the sandboxed filesystem; the result pickle at
``<result>`` reports ``("ok" | "error", message)`` back.

Cancellation is best-effort cooperative: the parent kills the process on
timeout/cancel (same as the builtin child), and the SIGTERM handler cancels
the runtime token first so code nodes calling ``check_cancellation`` unwind
cleanly before the kill lands.
"""

from __future__ import annotations

import logging
import pickle
import signal
import sys
import threading
from pathlib import Path

from server.app.executors.cancellation import CancellationToken
from server.app.executors.code import _load_run_from_source

logger = logging.getLogger(__name__)


def main() -> int:
    result_path = sys.argv[1]
    payload = pickle.load(sys.stdin.buffer)  # noqa: S301 - parent-produced payload
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)

    token = CancellationToken(threading.Event())

    def _cancel_and_exit(_signum, _frame) -> None:
        token.cancel()
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, _cancel_and_exit)

    runtime = payload["runtime"]
    runtime["cancellation"] = token
    prefix = f"[code:{runtime.get('node_key', '')}]"
    try:
        job_db_path = runtime.pop("_job_db_path", None)
        jobs_dir = runtime.pop("_jobs_dir", None)
        if job_db_path and jobs_dir:
            from server.app.jobs import JobQueries

            runtime["job_db"] = JobQueries(str(job_db_path), Path(jobs_dir))
        run = _load_run_from_source(payload["code"])
        logger.info(
            "%s start capability=%s custom (sandboxed)", prefix, runtime.get("capability", "")
        )
        run(payload["job"], Path(payload["job_dir"]), runtime)
        logger.info("%s completed", prefix)
        result: tuple[str, str | None] = ("ok", None)
    except BaseException as exc:  # the child must always report back
        result = ("error", f"{type(exc).__name__}: {exc}")
        logger.error("%s failed: %s", prefix, result[1])
        logger.exception("Sandboxed custom code node failed")

    tmp_path = f"{result_path}.tmp"
    with open(tmp_path, "wb") as handle:
        pickle.dump(result, handle)
    Path(tmp_path).replace(result_path)
    return 0 if result[0] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
