"""Member-readable transport contracts for run shared-resource confirmation."""

from __future__ import annotations

from pydantic import BaseModel


class SharedResourceRunConfirmationNodeRecord(BaseModel):
    node_key: str
    resource_ids: list[str]


class SharedResourceRunConfirmationResourceRecord(BaseModel):
    resource_id: str
    name: str
    version: int | None
    access_status: str


class SharedResourceRunConfirmationResponse(BaseModel):
    nodes: list[SharedResourceRunConfirmationNodeRecord]
    resources: list[SharedResourceRunConfirmationResourceRecord]
