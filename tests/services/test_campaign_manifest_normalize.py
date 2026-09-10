"""tests/services/test_campaign_manifest_normalize.py — manifest item normalization.

Ported from issue #505's tests/scripts/test_submit_campaign_manifest.py
(CLI → server module services/campaign_manifest.py, #532 PR-A): the
normalize_item half — the three-type contract, required-field validation,
str stripping, and source-located errors. Pure static (no_db).

姊妹文件：test_campaign_manifest_load.py（清单加载）、
test_campaign_manifest_guards.py（水位口径与参数护栏的平移基座）。
"""

from __future__ import annotations

import pytest

from server.app.services.campaign_manifest import ManifestError, normalize_item

pytestmark = pytest.mark.no_db


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
        with pytest.raises(ManifestError, match="不支持的 item type"):
            normalize_item({"type": "video"}, source="x")

    def test_missing_type_rejected(self):
        with pytest.raises(ManifestError, match="不支持的 item type"):
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
        with pytest.raises(ManifestError, match=field):
            normalize_item(item, source="x")

    def test_string_values_are_stripped(self):
        item = normalize_item(
            {"type": "ref", "connection_key": " cms ", "external_id": " Q-1 "}, source="x"
        )
        assert item["connection_key"] == "cms"
        assert item["external_id"] == "Q-1"

    def test_non_object_rejected(self):
        with pytest.raises(ManifestError, match="JSON object"):
            normalize_item(["material"], source="x")

    def test_error_message_carries_source(self):
        with pytest.raises(ManifestError, match="list.jsonl:3"):
            normalize_item({"type": "video"}, source="list.jsonl:3")

    def test_manifest_error_is_value_error(self):
        """路由层按 422 映射的类型基座：ManifestError 是 ValueError 族。"""
        assert issubclass(ManifestError, ValueError)


class TestRunItemContractRecheck:
    """审核二轮 P2：multipart 通道不再绕过 RunItem 合同——normalize 后每个
    item 过与 inline 路径相同的 discriminated 合同（extra=forbid、逐字段类型），
    无效形状 422 fail-fast，不留存为 job input。"""

    def test_ref_string_params_rejected(self):
        with pytest.raises(ManifestError, match="RunItem 合同"):
            normalize_item(
                {"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": "oops"},
                source="x",
            )

    def test_unknown_field_rejected(self):
        with pytest.raises(ManifestError, match="RunItem 合同"):
            normalize_item({"type": "material", "material_id": "m-1", "bogus": 1}, source="x")

    def test_material_wrong_typed_field_rejected(self):
        with pytest.raises(ManifestError, match="RunItem 合同"):
            normalize_item({"type": "material", "material_id": 123}, source="x")

    def test_valid_items_pass_through_contract(self):
        """合法形状不误拒，且返回合同化后的规范形。"""
        assert normalize_item({"type": "material", "material_id": "m-1"}, source="x") == {
            "type": "material",
            "material_id": "m-1",
        }
        assert normalize_item({"type": "bundle", "bundle_id": "b-1"}, source="x") == {
            "type": "bundle",
            "bundle_id": "b-1",
        }
        assert normalize_item(
            {"type": "ref", "connection_key": "cms", "external_id": "Q-1"}, source="x"
        ) == {"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": {}}

    def test_contract_error_message_locates_source_line(self):
        with pytest.raises(ManifestError, match="list.jsonl:7: item 不符合 RunItem 合同"):
            normalize_item(
                {"type": "ref", "connection_key": "cms", "external_id": "Q-1", "params": []},
                source="list.jsonl:7",
            )
