"""Code defaults for the retired ``config/agent_legion.yaml`` ``openclaw:`` section.

Values equal the last tracked yaml values. The admin-tunable copy lives in
the DB instance settings document; these defaults back both the typed
runtime config (filled into the config dict at load) and the default DB
document. Env overrides (``AGENT_LEGION_OPENCLAW_CWD``) and explicit
single-file configs win: only missing keys are filled.
"""

from __future__ import annotations

import copy
from typing import Any

# Path whitelist only in skill_safety: refs are pinned by config/skills.lock
# (single source of truth, config governance G3). A ref key is rejected.
DEFAULT_OPENCLAW_CONFIG: dict[str, Any] = {
    "cwd": ".",
    "timeout_seconds": 600,
    "isolated_workspace_root": "",
    "command_template": [
        "openclaw",
        "agent",
        "--local",
        "--agent",
        "main",
        "--session-id",
        "{video_id}-{timestamp}",
        "--thinking",
        "on",
        "--message",
        "{prompt_text}",
        "--json",
    ],
    "skill_safety": {
        "enabled": True,
        "repos": [
            {"path": "~/.agents/skills/agent-legion/video_knowledge/generate_interactions"},
            {"path": "~/.agents/skills/agent-legion/video_knowledge/review_video_content"},
            {"path": "~/.agents/skills/agent-legion/video_knowledge/review_subtitles"},
            {"path": "~/.agents/skills/agent-legion/video_knowledge/generate_chapters"},
        ],
    },
}


def apply_openclaw_config_defaults(config: dict[str, Any]) -> None:
    """Fill the retired ``openclaw:`` code defaults into the config dict."""
    openclaw = config.get("openclaw")
    if not isinstance(openclaw, dict):
        config["openclaw"] = openclaw = {}
    for key, value in DEFAULT_OPENCLAW_CONFIG.items():
        if key == "skill_safety":
            skill_safety = openclaw.get("skill_safety")
            if not isinstance(skill_safety, dict):
                openclaw["skill_safety"] = skill_safety = {}
            for safety_key, safety_value in value.items():
                skill_safety.setdefault(safety_key, copy.deepcopy(safety_value))
        else:
            openclaw.setdefault(key, copy.deepcopy(value))
