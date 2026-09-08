"""Unit tests for scripts/submit_campaign.py (#505)——清单解析。

纯静态单测（no_db）：item 规整（normalize_item 的三型契约 / 必填校验 /
str 化）与清单加载（load_items 的 jsonl / csv 路径、混合表头空列丢弃、
报错定位到 行/文件）。

姊妹文件：test_submit_campaign.py（投放循环）、test_submit_campaign_guards.py
（水位口径与参数护栏）、test_submit_campaign_http_cli.py（HTTP 层与 CLI）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.submit_campaign import (  # noqa: E402
    UsageError,
    load_items,
    normalize_item,
)

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# normalize_item
# ---------------------------------------------------------------------------


class TestNormalizeItem:
    def test_material_item_passes_through(self):
        item = normalize_item({"type": "material", "material_id": "mat-1"}, source="x")
        assert item == {"type": "material", "material_id": "mat-1"}

    def test_ref_item_gets_default_params(self):
        item = normalize_item(
            {"type": "ref", "connection_key": "cms", "external_id": "Q-1"}, source="x"
        )
        assert item["params"] == {}
        assert item["connection_key"] == "cms"
        assert item["external_id"] == "Q-1"

    def test_ref_item_keeps_explicit_params(self):
        item = normalize_item(
            {
                "type": "ref",
                "connection_key": "cms",
                "external_id": "Q-1",
                "params": {"lang": "zh"},
            },
            source="x",
        )
        assert item["params"] == {"lang": "zh"}

    def test_bundle_item(self):
        item = normalize_item({"type": "bundle", "bundle_id": "b-1"}, source="x")
        assert item == {"type": "bundle", "bundle_id": "b-1"}

    def test_unknown_type_rejected(self):
        with pytest.raises(UsageError, match="不支持的 item type"):
            normalize_item({"type": "video"}, source="x")

    def test_missing_type_rejected(self):
        with pytest.raises(UsageError, match="不支持的 item type"):
            normalize_item({"material_id": "mat-1"}, source="x")

    @pytest.mark.parametrize(
        ("item", "field"),
        [
            ({"type": "material"}, "material_id"),
            ({"type": "material", "material_id": "  "}, "material_id"),
            ({"type": "bundle"}, "bundle_id"),
            ({"type": "ref", "external_id": "Q-1"}, "connection_key"),
            ({"type": "ref", "connection_key": "cms"}, "external_id"),
        ],
    )
    def test_missing_required_field_rejected(self, item, field):
        with pytest.raises(UsageError, match=field):
            normalize_item(item, source="x")

    def test_string_values_are_stripped(self):
        item = normalize_item(
            {"type": "ref", "connection_key": " cms ", "external_id": " Q-1 "}, source="x"
        )
        assert item["connection_key"] == "cms"
        assert item["external_id"] == "Q-1"

    def test_non_object_rejected(self):
        with pytest.raises(UsageError, match="JSON object"):
            normalize_item(["material"], source="x")

    def test_error_message_carries_source(self):
        with pytest.raises(UsageError, match="list.jsonl:3"):
            normalize_item({"type": "video"}, source="list.jsonl:3")


# ---------------------------------------------------------------------------
# load_items
# ---------------------------------------------------------------------------


class TestLoadItems:
    def test_jsonl_loads_in_order(self, tmp_path: Path):
        path = tmp_path / "campaign.jsonl"
        path.write_text(
            '{"type": "material", "material_id": "m-1"}\n'
            "# 注释行跳过\n"
            "\n"
            '{"type": "ref", "connection_key": "cms", "external_id": "Q-1"}\n',
            encoding="utf-8",
        )
        items = load_items(path)
        assert [item["type"] for item in items] == ["material", "ref"]
        assert items[1]["params"] == {}

    def test_jsonl_invalid_json_reports_line(self, tmp_path: Path):
        path = tmp_path / "campaign.jsonl"
        path.write_text('{"type": "material", "material_id": "m-1"}\n{oops\n', encoding="utf-8")
        with pytest.raises(UsageError, match="campaign.jsonl:2"):
            load_items(path)

    def test_csv_loads_rows(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,material_id\nmaterial,m-1\nmaterial,m-2\n", encoding="utf-8")
        items = load_items(path)
        # csv 每行变成 {列头: 值}，走 normalize_item 规整（str 化 + 必填校验）
        assert [item["material_id"] for item in items] == ["m-1", "m-2"]
        assert all(item["type"] == "material" for item in items)

    def test_csv_ref_item_gets_params(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,connection_key,external_id\nref,cms,Q-1\n", encoding="utf-8")
        items = load_items(path)
        assert items[0]["params"] == {}

    def test_csv_missing_field_reports_row(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,material_id\nmaterial,\n", encoding="utf-8")
        with pytest.raises(UsageError, match="campaign.csv:2"):
            load_items(path)

    def test_csv_blank_rows_skipped(self, tmp_path: Path):
        path = tmp_path / "campaign.csv"
        path.write_text("type,material_id\n,\nmaterial,m-1\n", encoding="utf-8")
        items = load_items(path)
        assert len(items) == 1

    def test_csv_mixed_type_header_drops_empty_columns(self, tmp_path: Path):
        """混合表头：三型行共用一张表，空列不污染他类型字段（codex #531 P2-2）。

        DictReader 给 material 行附带 bundle_id="" 等空列；CSV 空单元格只能
        表达「字段缺省」（POST /runs 契约对缺省字段走默认值，显式空串列因
        extra="forbid" / min_length=1 必 422），进 normalize 前丢弃。
        """
        path = tmp_path / "campaign.csv"
        path.write_text(
            "type,material_id,bundle_id,connection_key,external_id\n"
            "material,m-1,,,\n"
            "bundle,,b-1,,\n"
            "ref,,,cms,Q-1\n",
            encoding="utf-8",
        )
        items = load_items(path)
        assert items == [
            {"type": "material", "material_id": "m-1"},
            {"type": "bundle", "bundle_id": "b-1"},
            {"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": {}},
        ]

    def test_missing_file_rejected(self, tmp_path: Path):
        with pytest.raises(UsageError, match="清单文件不存在"):
            load_items(tmp_path / "nope.jsonl")

    def test_empty_list_rejected(self, tmp_path: Path):
        path = tmp_path / "campaign.jsonl"
        path.write_text("# 只有注释\n", encoding="utf-8")
        with pytest.raises(UsageError, match="没有可用 item"):
            load_items(path)

    def test_hundred_thousand_items_load(self, tmp_path: Path):
        """几十万级清单的加载冒烟（issue 的目标规模）。"""
        path = tmp_path / "big.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for index in range(100_000):
                fh.write(
                    json.dumps(
                        {"type": "ref", "connection_key": "cms", "external_id": f"Q-{index}"}
                    )
                    + "\n"
                )
        items = load_items(path)
        assert len(items) == 100_000
        assert items[0]["external_id"] == "Q-0"
        assert items[-1]["external_id"] == "Q-99999"
