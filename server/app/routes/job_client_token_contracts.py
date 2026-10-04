"""Read-only ``client_token`` view fields for job responses (#925).

Split out of ``job_view_contracts`` for the file-size budget. The item-level
``client_token`` (#813) never enters the job ``input`` document; a persisted
job records it only as the ``~<token>`` suffix of ``source_id``. These fields
expose the server-parsed token (``split_scoped_source_id``, the inverse of
``scoped_entity_id``) so clients never split the string themselves.
"""

from pydantic import BaseModel, Field, model_validator

from server.app.services.run_item_client_token import split_scoped_source_id


class JobClientTokenFields(BaseModel):
    """Mixin for job summary models carrying ``source_type`` / ``source_id``.

    Both fields are always derived after validation — any submitted value is
    overwritten — so they stay read-only whatever the row dict carried.
    """

    client_token: str | None = Field(
        default=None,
        description=(
            "条目级幂等键（#813）：material / bundle（含 text 归一的 material）条目"
            "带 client_token 提交时为该 token，否则为 null；ref 条目恒为 null。"
            "由服务端从 source_id 的 `~<token>` 后缀解析，只读。"
        ),
    )
    source_base_id: str | None = Field(
        default=None,
        description=(
            "去掉 client_token 作用域后的条目 id（material_id / bundle_id）；"
            "无 token 时等于 source_id。同一材料以不同 client_token 提交的多个 "
            "job 共享同一 source_base_id。只读。"
        ),
    )

    @model_validator(mode="after")
    def _derive_client_token(self) -> "JobClientTokenFields":
        self.source_base_id, self.client_token = split_scoped_source_id(
            str(getattr(self, "source_type", "")), str(getattr(self, "source_id", ""))
        )
        return self
