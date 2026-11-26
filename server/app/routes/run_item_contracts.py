"""Run item contracts (materials-and-runs design §4).

Split out of ``run_contracts`` for the file-size budget when #813 added the
optional ``client_token`` to material / bundle / text items.
"""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_item_client_token import (
    CLIENT_TOKEN_MAX_CHARS,
    CLIENT_TOKEN_PATTERN,
)
from server.app.services.run_item_params import validate_item_params

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


def _checked_params(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_item_params(value)
    except InvalidOperationError as exc:
        raise ValueError(str(exc)) from exc


_NonSecretParams = Annotated[
    dict[str, Any],
    Field(
        default_factory=dict,
        description=(
            "可选的非敏感 JSON 参数，原样冻结进 job input，节点经 SDK batch payload 读取。"
            "单条目 canonical JSON 不超过 16 KiB，深度/键值/数组有限制；"
            "禁止凭据字段与 vault secret marker。secret 仍走节点 config_schema/vault。"
        ),
    ),
    AfterValidator(_checked_params),
]


class RunItemMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material"]
    material_id: str = Field(min_length=1)
    client_token: _ClientToken = None
    params: _NonSecretParams


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
    params: _NonSecretParams


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
