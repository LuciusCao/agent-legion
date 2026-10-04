"""#925: ``split_scoped_source_id`` is the read-side inverse of ``scoped_entity_id``.

Job responses expose the item-level ``client_token`` (#813) as a structured
field parsed by the server from ``source_id``; these pins keep the parse rule
in lockstep with the write-side derivation.
"""

from __future__ import annotations

import pytest

from server.app.routes.job_view_contracts import JobSummaryResponse
from server.app.services.run_item_client_token import (
    CLIENT_TOKEN_MAX_CHARS,
    scoped_entity_id,
    split_scoped_source_id,
)

pytestmark = pytest.mark.no_db

MATERIAL_ID = "0123456789abcdef0123456789abcdef"
BUNDLE_ID = "fedcba9876543210fedcba9876543210"


@pytest.mark.parametrize(
    ("source_type", "entity_id", "item"),
    [
        ("material", MATERIAL_ID, {"type": "material", "material_id": MATERIAL_ID}),
        ("bundle", BUNDLE_ID, {"type": "bundle", "bundle_id": BUNDLE_ID}),
        # text 归一为内容寻址 material：token 随归一后的 material 条目走
        ("material", MATERIAL_ID, {"type": "text", "content": "x"}),
    ],
)
@pytest.mark.parametrize("token", ["order-1001", "a", "A.b_c-9", "z" * CLIENT_TOKEN_MAX_CHARS])
def test_split_inverts_scoped_entity_id(source_type, entity_id, item, token) -> None:
    source_id = scoped_entity_id(entity_id, {**item, "client_token": token})
    assert source_id == f"{entity_id}~{token}"
    assert split_scoped_source_id(source_type, source_id) == (entity_id, token)


@pytest.mark.parametrize(
    ("source_type", "entity_id", "item"),
    [
        ("material", MATERIAL_ID, {"type": "material", "material_id": MATERIAL_ID}),
        ("bundle", BUNDLE_ID, {"type": "bundle", "bundle_id": BUNDLE_ID}),
        ("material", MATERIAL_ID, {"type": "text", "content": "x"}),
        ("material", MATERIAL_ID, {"type": "material", "client_token": None}),
    ],
)
def test_split_without_token_is_identity(source_type, entity_id, item) -> None:
    source_id = scoped_entity_id(entity_id, item)
    assert source_id == entity_id
    assert split_scoped_source_id(source_type, source_id) == (entity_id, None)


@pytest.mark.parametrize(
    "source_id",
    [
        "conn:ext-1",
        # ref 的 external_id 由调用方控制，可能自带 `~`——即使形如 token 也不解析
        "conn:ext~order-1001",
        "conn:~abc",
    ],
)
def test_ref_sources_never_parse(source_id) -> None:
    assert split_scoped_source_id("ref", source_id) == (source_id, None)


@pytest.mark.parametrize("source_type", ["question", "video", "knowledge", ""])
def test_legacy_source_types_never_parse(source_type) -> None:
    assert split_scoped_source_id(source_type, f"{MATERIAL_ID}~tok") == (
        f"{MATERIAL_ID}~tok",
        None,
    )


@pytest.mark.parametrize(
    "source_id",
    [
        f"{MATERIAL_ID}~",  # 空 token
        f"~{'t' * 3}",  # 空 id
        f"{MATERIAL_ID}~-lead",  # 首字符非字母数字
        f"{MATERIAL_ID}~a~b",  # token 字符集不含 ~
        f"{MATERIAL_ID}~a/b",
        f"{MATERIAL_ID}~{'z' * (CLIENT_TOKEN_MAX_CHARS + 1)}",
    ],
)
def test_malformed_suffix_is_not_a_token(source_id) -> None:
    assert split_scoped_source_id("material", source_id) == (source_id, None)


def _summary(source_type: str, source_id: str, **extra) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=f"ws_ws_{source_id}",
        workspace_id="ws",
        workflow_key="ws",
        source_type=source_type,
        source_id=source_id,
        batch_id="",
        title="doc.txt",
        status="queued",
        storage_dir="",
        error_message="",
        created_at="",
        updated_at="",
        **extra,
    )


def test_job_summary_exposes_parsed_token() -> None:
    dumped = _summary("material", f"{MATERIAL_ID}~order-1001").model_dump()
    assert dumped["client_token"] == "order-1001"
    assert dumped["source_base_id"] == MATERIAL_ID


def test_job_summary_without_token_and_ref() -> None:
    plain = _summary("bundle", BUNDLE_ID)
    assert (plain.client_token, plain.source_base_id) == (None, BUNDLE_ID)
    ref = _summary("ref", "conn:ext~order-1001")
    assert (ref.client_token, ref.source_base_id) == (None, "conn:ext~order-1001")


def test_job_summary_fields_are_server_derived() -> None:
    # 只读：调用方/存储层给的值一律被 source_id 解析结果覆盖
    forged = _summary("ref", "conn:ext", client_token="forged", source_base_id="x")
    assert (forged.client_token, forged.source_base_id) == (None, "conn:ext")
