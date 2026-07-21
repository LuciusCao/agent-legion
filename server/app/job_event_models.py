from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

JobEventKind = Literal["updated", "created", "deleted"]


@dataclass(frozen=True)
class JobEvent:
    revision: int
    workspace_id: str
    job_id: str
    kind: JobEventKind


@dataclass(frozen=True)
class CompactedJobEvents:
    latest_revision: int
    updated_job_ids_by_workspace: dict[str, set[str]] = field(default_factory=dict)
    created_job_ids_by_workspace: dict[str, set[str]] = field(default_factory=dict)
    deleted_job_ids_by_workspace: dict[str, set[str]] = field(default_factory=dict)
    resync_workspace_ids: set[str] = field(default_factory=set)
