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
        logger.debug("openclaw agents list failed", exc_info=True)
        return []
