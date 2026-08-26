"""Code defaults for the retired ``config/agent_legion.yaml`` ``openclaw:`` section.

Only ``cwd`` remains; the other retired-yaml knobs (command_template,
timeout_seconds, isolated_workspace_root, skill_safety) were configurable
but never consumed and have been removed. The admin-tunable copy lives in
the DB instance settings document; these defaults back both
the typed runtime config (filled into the config dict at load) and the
default DB document. Env overrides (``AGENT_LEGION_OPENCLAW_CWD``) and
explicit single-file configs win: only missing keys are filled.
"""

from __future__ import annotations

import copy
from typing import Any

# Only ``cwd`` remains consumed (agent discovery working directory). The
# command_template / timeout_seconds / isolated_workspace_root / skill_safety
# knobs retired with the legacy business workflow pipeline.
DEFAULT_OPENCLAW_CONFIG: dict[str, Any] = {"cwd": "."}


def apply_openclaw_config_defaults(config: dict[str, Any]) -> None:
    """Fill the retired ``openclaw:`` code defaults into the config dict."""
    openclaw = config.get("openclaw")
    if not isinstance(openclaw, dict):
        config["openclaw"] = openclaw = {}
    for key, value in DEFAULT_OPENCLAW_CONFIG.items():
        openclaw.setdefault(key, copy.deepcopy(value))
