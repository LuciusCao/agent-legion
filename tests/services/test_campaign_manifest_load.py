"""tests/services/test_campaign_manifest_load.py — manifest loading + round-trip.

Ported from issue #505's tests/scripts/test_submit_campaign_manifest.py
(load_items half, #532 PR-A): jsonl/csv parsing, mixed-type header empty
column dropping, error location to file:line, plus the server-side additions
serialize_manifest / parse_manifest_text (the canonical jsonl round-trip the
stored spec uses). Pure static (no_db).
"""

from __future__ import annotations

import json

import pytest

from server.app.services.campaign_manifest import (
    ManifestError,
    load_items_text,
    normalize_item,
    parse_manifest_text,
    serialize_manifest,
)

pytestmark = pytest.mark.no_db


class TestLoadItemsText:
    def test_jsonl_loads_in_order(self):
        text = (
            '{"type": "material", "material_id": "m-1"}\n'
            "# 注释行跳过\n"
            "\n"
            '{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}\n'
        )
        items = load_items_text(text, filename="campaign.jsonl")
        assert [item["type"] for item in items] == ["material", "ref"]
        assert items[1]["params"] == {}

    def test_jsonl_invalid_json_reports_line(self):
        text = '{"type": "material", "material_id": "m-1"}\n{oops\n'
        with pytest.raises(ManifestError, match="campaign.jsonl:2"):
            load_items_text(text, filename="campaign.jsonl")

    def test_csv_loads_rows(self):
        text = "type,material_id\nmaterial,m-1\nmaterial,m-2\n"
        items = load_items_text(text, filename="campaign.csv")
        # csv 每行变成 {列头: 值}，走 normalize_item 规整（str 化 + 必填校验）
        assert [item["material_id"] for item in items] == ["m-1", "m-2"]
        assert all(item["type"] == "material" for item in items)

    def test_csv_ref_item_gets_params(self):
        text = "type,connection_key,external_id\nref,cms,Q-1\n"
        items = load_items_text(text, filename="campaign.csv")
        assert items[0]["params"] == {}

    def test_csv_missing_field_reports_row(self):
        text = "type,material_id\nmaterial,\n"
        with pytest.raises(ManifestError, match="campaign.csv:2"):
            load_items_text(text, filename="campaign.csv")

    def test_csv_blank_rows_skipped(self):
        text = "type,material_id\n,\nmaterial,m-1\n"
        items = load_items_text(text, filename="campaign.csv")
        assert len(items) == 1

    def test_csv_mixed_type_header_drops_empty_columns(self):
        """混合表头：三型行共用一张表，空列不污染他类型字段（codex #531 P2-2）。

        DictReader 给 material 行附带 bundle_id="" 等空列；CSV 空单元格只能
        表达「字段缺省」（POST /runs 契约对缺省字段走默认值，显式空串列因
        extra="forbid" / min_length=1 必 422），进 normalize 前丢弃。
        """
        text = (
            "type,material_id,bundle_id,connection_key,external_id\n"
            "material,m-1,,,\n"
            "bundle,,b-1,,\n"
            "ref,,,cms,Q-1\n"
        )
        items = load_items_text(text, filename="campaign.csv")
        assert items == [
            {"type": "material", "material_id": "m-1"},
            {"type": "bundle", "bundle_id": "b-1"},
            {"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": {}},
        ]

    def test_csv_utf8_bom_stripped(self):
        """带 BOM 的 csv（Excel 导出常态）表头首列不能残留 \\ufeff。"""
        text = "﻿type,material_id\nmaterial,m-1\n"
        items = load_items_text(text, filename="campaign.csv")
        assert items == [{"type": "material", "material_id": "m-1"}]

    def test_csv_blank_rows_only_rejected(self):
        text = "type,material_id\n,\n,\n"
        with pytest.raises(ManifestError, match="没有可用 item"):
            load_items_text(text, filename="campaign.csv")

    def test_empty_list_rejected(self):
        with pytest.raises(ManifestError, match="没有可用 item"):
            load_items_text("# 只有注释\n", filename="campaign.jsonl")

    def test_hundred_thousand_items_load(self):
        """几十万级清单的加载冒烟（issue 的目标规模）。"""
        text = "".join(
            json.dumps({"type": "ref", "connection_key": "cms", "external_id": f"Q-{index}"}) + "\n"
            for index in range(100_000)
        )
        items = load_items_text(text, filename="big.jsonl")
        assert len(items) == 100_000
        assert items[0]["external_id"] == "Q-0"
        assert items[-1]["external_id"] == "Q-99999"


class TestManifestRoundTrip:
    def test_serialize_then_parse_round_trips(self):
        items = [
            {"type": "material", "material_id": "m-1"},
            {"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": {}},
        ]
        payload = serialize_manifest(items)
        assert payload.count("\n") == 2
        assert parse_manifest_text(payload) == items

    def test_serialized_form_is_sorted_keys_jsonl(self):
        """存储形态：规整后的 item → sort_keys jsonl（params 默认值已补齐）。"""
        items = [
            {
                "type": "ref",
                "external_id": "Q-1",
                "connection_key": "cms",
            }
        ]
        payload = serialize_manifest([normalize_item(items[0], source="x")])
        line = json.loads(payload.strip())
        assert list(line.keys()) == ["connection_key", "external_id", "params", "type"]
        assert line["params"] == {}

    def test_inline_ceiling_semantics(self):
        """行内通道的体量语义：serialized 字节数即 target_spec 的存储体量。"""
        items = [{"type": "material", "material_id": f"m-{index:05d}"} for index in range(1000)]
        payload = serialize_manifest(items)
        assert len(payload.encode("utf-8")) > 26_000  # ~2.6 万字节 / 千 item 量级
