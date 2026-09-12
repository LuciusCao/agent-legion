"""Contracts for the studio-agent shared skill material tools (#633).

``_shared`` lives under the workspace skill dir and holds the materials a
workspace's skills share: ``map.json`` (the materials→skills mapping) plus
``references/`` and ``scripts/`` payloads. ``map.json`` is authored as just
one file of the write payload (the agent owns the JSON); the read returns
the parsed map alongside the readable files.
"""

from typing import Any

from pydantic import BaseModel, Field

# Same per-file bounds as the skill save (studio_agent_skill_contracts).
MAX_FILES = 100
MAX_FILE_CHARS = 128 * 1024


class SharedMaterialFileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=MAX_FILE_CHARS)


class SharedMaterialsSaveRequest(BaseModel):
    files: list[SharedMaterialFileWrite] = Field(min_length=1, max_length=MAX_FILES)


class SharedMaterialFile(BaseModel):
    """One readable shared file — same shape as the skill detail read."""

    path: str
    size: int = Field(ge=0)
    content: str
    truncated: bool = False


class SharedMaterialsResponse(BaseModel):
    """Workspace shared materials; ``map: null`` + empty ``files`` is the
    structured empty state for a workspace that never opted into ``_shared``."""

    workspace_id: str
    map: dict[str, Any] | None = None
    files: list[SharedMaterialFile] = Field(default_factory=list)
