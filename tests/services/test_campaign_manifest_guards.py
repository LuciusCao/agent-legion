"""tests/services/test_campaign_manifest_guards.py — watermark/guard semantics.

Ported from issue #505's tests/scripts/test_submit_campaign_guards.py
(#532 PR-A). The non_terminal_count wide-set watermark definition moved into
the CAMPAIGN-STATE-001 invariant statement (the feeder applies it per tick in
PR-B); this port pins the same set semantics plus the campaign config knob
contract the service validates against (watermark is a replenishment trigger
level, not a capacity promise — a low watermark + large batch is a legal
burst configuration; the first batch is never refused for lack of headroom).
Pure static (no_db).
"""

from __future__ import annotations

import pytest

from server.app.configuration.executor_runtime import CampaignsRuntimeConfig
from server.app.services.campaign_manifest import ManifestError

pytestmark = pytest.mark.no_db

# 幂等口径的「非终态」（宽口径，对齐 #349 红线的集合定义）：
# total - completed - failed。paused / awaiting_approval 占着非终态集合，
# 投放侧不得把它们误判为「还有水位余量」。feeder（PR-B）每 tick 按此求和。
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed"})


def non_terminal_count(job_stats: dict[str, int]) -> int:
    return sum(count for status, count in job_stats.items() if status not in TERMINAL_JOB_STATUSES)


class TestNonTerminalCount:
    def test_terminal_statuses_excluded(self):
        stats = {"completed": 1000, "failed": 50, "running": 200, "pending": 500}
        assert non_terminal_count(stats) == 700

    def test_paused_and_awaiting_approval_counted(self):
        """宽口径：paused / awaiting_approval 占着非终态集合，计入水位。"""
        stats = {"completed": 10, "paused": 5, "awaiting_approval": 7}
        assert non_terminal_count(stats) == 12

    def test_unknown_statuses_counted(self):
        """未知状态按非终态计（保守：宁可少投不可超红线的集合口径）。"""
        assert non_terminal_count({"weird": 3, "completed": 1}) == 3

    def test_empty_stats(self):
        assert non_terminal_count({}) == 0


class TestCampaignsRuntimeConfig:
    """设计 §2.5 的参数表钉死：默认值是 #505 CLI 的现场标定。"""

    def test_defaults_match_design_table(self):
        config = CampaignsRuntimeConfig()
        assert config.feed_interval_seconds == 10
        assert config.feeder_tick_seconds == 2
        assert config.default_watermark == 30_000
        assert config.default_batch_size == 5_000
        assert config.rerun_max_batch_size == 5_000
        assert config.max_active_per_workspace == 3
        assert config.manifest_inline_max_bytes == 262_144
        assert config.manifest_max_bytes == 52_428_800
        assert config.manifest_cache_max_bytes == 268_435_456

    def test_watermark_low_with_large_batch_is_legal(self):
        """低水位线 + 大批次合法（codex #531 P2-1 的服务端化）：
        水位线是补货触发阈值而非容量承诺——配置层不拒绝该组合，
        启动期也不做「至少容纳一批」的拒绝。"""
        CampaignsRuntimeConfig(default_watermark=100, default_batch_size=5_000)

    def test_non_positive_watermark_rejected(self):
        with pytest.raises(ValueError):
            CampaignsRuntimeConfig(default_watermark=0)

    def test_non_positive_batch_size_rejected(self):
        with pytest.raises(ValueError):
            CampaignsRuntimeConfig(default_batch_size=0)

    def test_negative_manifest_cache_budget_rejected(self):
        """0 = 不限（与 max_items_per_run 同约定），负数不是合法预算。"""
        CampaignsRuntimeConfig(manifest_cache_max_bytes=0)
        with pytest.raises(ValueError):
            CampaignsRuntimeConfig(manifest_cache_max_bytes=-1)


class TestManifestErrorContract:
    def test_manifest_error_importable_and_value_error(self):
        assert issubclass(ManifestError, ValueError)
