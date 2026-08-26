"""Admin API contract for the OpenClaw block of the instance settings document.

Only ``cwd`` remains consumed (agent discovery working directory and its
startup validation). The command_template / timeout_seconds /
isolated_workspace_root / skill_safety knobs retired with the legacy
business workflow pipeline.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class InstanceOpenClawSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cwd: str
