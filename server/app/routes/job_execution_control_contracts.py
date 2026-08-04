from typing import Literal

from pydantic import BaseModel


class ExecutionControlSummaryResponse(BaseModel):
    mode: Literal["full", "until_node"] = "full"
    target_node_key: str | None = None
    paused: bool = False
    pause_reason: str = ""
