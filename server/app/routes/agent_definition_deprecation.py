"""Deprecation marker for the Agent definition write endpoints (#935).

#440 P3 (D3): Agent definitions no longer supply node execution profiles —
the publish gate requires self-contained agent nodes — so the catalog's
write endpoints (create / draft / publish / rollback / copy / archive) are
deprecated and removed in P4. Split from ``agent_definitions`` (file
budget): every write route spreads ``DEPRECATED_WRITE`` into its decorator,
which flags it in OpenAPI and answers with a ``Deprecation`` header plus a
human-readable pointer to the node profile. Read endpoints stay plain.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Response

DEPRECATION_NOTICE = (
    "Agent definitions are retired as node execution profiles (#440): declare"
    " execution.runtime / tools / requires_labels / config_schema / skill on the"
    " workflow agent node instead; this write endpoint will be removed"
)


def _mark_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["X-Agent-Legion-Deprecation"] = DEPRECATION_NOTICE


DEPRECATED_WRITE: dict[str, Any] = {
    "deprecated": True,
    "dependencies": [Depends(_mark_deprecated)],
}
