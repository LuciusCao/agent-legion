"""Run item contracts (materials-and-runs design §4).

Split out of ``run_contracts`` for the file-size budget when #813 added the
optional ``client_token`` to material / bundle / text items.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.app.services.run_item_client_token import (
    CLIENT_TOKEN_MAX_CHARS,
    CLIENT_TOKEN_PATTERN,
)

# #813: optional item-level idempotency key (material / bundle / text only;
# ref items are namespaced by connection_key:external_id already).
_ClientToken = Annotated[
    str | None,
    Field(
        default=None,
        min_length=1,
        max_length=CLIENT_TOKEN_MAX_CHARS,
        pattern=CLIENT_TOKEN_PATTERN,
        description=(
            "可选的条目级幂等键（#813）：参与 job 身份与 run digest 派生。"
            "同内容不同 token 各成独立 job；同 token 重复提交幂等命中同一 job；"
            "不传保持纯内容寻址。1-64 字符，[A-Za-z0-9._-]，首字符为字母或数字。"
        ),
    ),
]


class RunItemMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material"]
    material_id: str = Field(min_length=1)
    client_token: _ClientToken = None


class RunItemRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ref"]
    connection_key: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class RunItemBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bundle"]
    bundle_id: str = Field(min_length=1)
    client_token: _ClientToken = None


class RunItemText(BaseModel):
    """Requirement text typed inline; persisted as a material before resolution."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    content: str = Field(min_length=1, max_length=65536)
    filename: str | None = Field(default=None, max_length=255)
    client_token: _ClientToken = None


RunItem = Annotated[
    RunItemMaterial | RunItemRef | RunItemBundle | RunItemText, Field(discriminator="type")
]
