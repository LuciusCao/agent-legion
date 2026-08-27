"""Admin API contract for the OpenClaw block of the instance settings document.

Only ``cwd`` remains (existence-checked at startup validation). The
command_template / timeout_seconds / isolated_workspace_root / skill_safety
knobs retired with the legacy business workflow pipeline: stored documents
are normalized at read time, PUT rejects them with 422.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class InstanceOpenClawSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cwd: str
