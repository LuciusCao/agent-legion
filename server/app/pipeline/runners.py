import json
import subprocess
from typing import Any


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
        return []
