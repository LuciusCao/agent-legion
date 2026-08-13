"""Sandboxed custom code node child entry (EXEC-CODE-003).

Spawned as ``python -m workspace_libs.code_child <result>`` inside
the velites ``sandbox wrap`` confinement: custom node code is user-supplied
(EXEC-CODE-002) and must never run with the server process's full privileges.
The payload pickle arrives on **stdin** (job, job_dir, runtime) so resolved
secrets never touch the sandboxed filesystem; the result at ``<result>`` is
**JSON** (``{"status": "ok"|"error", "message": str|null}``) — never pickle,
because the file sits in a sandbox-writable directory and the parent must not
deserialize anything the child tree could have replaced.

Custom children get no database handle: DB-derived inputs (batch payload,
skill versions) are prefetched by the parent into ``runtime["job_batch"]`` /
``runtime["skill_versions"]`` — the same contract builtin children run under.

Cancellation is best-effort cooperative: the parent kills the process group
on timeout/cancel, and the SIGTERM handler cancels the runtime token first so
code nodes calling ``check_cancellation`` unwind cleanly before the kill
lands.

This module lives in ``workspace_libs`` (zero ``server.app`` imports) so the
same entry can run on a Worker from a bare bundle snapshot without a repo
checkout.
"""

from __future__ import annotations

import json
import logging
import pickle
import signal
import sys
import threading
from pathlib import Path

from workspace_libs.cancellation import CancellationToken
from workspace_libs.code_loader import _load_run_from_source

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
        run = _load_run_from_source(payload["code"])
        logger.info(
            "%s start capability=%s custom (sandboxed)", prefix, runtime.get("capability", "")
        )
        run(payload["job"], Path(payload["job_dir"]), runtime)
        logger.info("%s completed", prefix)
        result = {"status": "ok", "message": None}
    except BaseException as exc:  # the child must always report back
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        logger.error("%s failed: %s", prefix, result["message"])
        logger.exception("Sandboxed custom code node failed")

    tmp_path = f"{result_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    Path(tmp_path).replace(result_path)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
