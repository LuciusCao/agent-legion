"""OpenClaw agent discovery for the agent status manager.

Platform-level helper extracted from the retired ``server.app.pipeline``
package: shells out to the openclaw CLI to enumerate locally available
agents. Any failure (CLI missing, non-zero exit, invalid JSON) yields an
empty list — discovery is best-effort.
"""

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def list_openclaw_agents(timeout: int = 10) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["openclaw", "agents", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return [a for a in data if isinstance(a, dict) and "id" in a]
    except Exception:
        # #204 broad-except audit: best-effort local discovery over an
        # external CLI surface whose outcome space is not enumerable here:
        # FileNotFoundError (CLI absent) and OSError from subprocess.run,
        # TimeoutExpired, json.JSONDecodeError on non-JSON stdout, plus
        # TypeError from a non-iterable/oddly-shaped payload — the CLI's
        # output is untrusted input, so a shape surprise is a discovery
        # failure, not a programming error. Empty list is the module
        # contract (the status panel shows no rows); the registry's
        # discover() catch (#204-audited) relies on exactly that.
        # exc_info keeps the traceback at debug level (CLI-absent is the
        # common degraded case, not an error).
        logger.debug("openclaw agents list failed", exc_info=True)
        return []
