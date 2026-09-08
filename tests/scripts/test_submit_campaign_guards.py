"""Unit tests for scripts/submit_campaign.py (#505)——水位口径与参数护栏。

纯静态单测（no_db）：non_terminal_count 的水位集合口径（宽口径对齐
#349 红线：一切未 settled 的 job），check_batch_size / check_watermark
的参数护栏（水位线是补货触发阈值而非容量承诺——低水位线 + 大批次是
合法的突发配置）。

姊妹文件：test_submit_campaign.py（投放循环）、test_submit_campaign_manifest.py
（清单解析）、test_submit_campaign_http_cli.py（HTTP 层与 CLI）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.submit_campaign import (  # noqa: E402
    UsageError,
    check_batch_size,
    check_watermark,
    non_terminal_count,
)

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# non_terminal_count（水位口径）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 参数护栏
# ---------------------------------------------------------------------------


class TestGuards:
    def test_batch_size_over_run_limit_rejected(self):
        with pytest.raises(UsageError, match="max_items_per_run"):
            check_batch_size(20_001, 20_000)

    def test_batch_size_at_run_limit_ok(self):
        check_batch_size(20_000, 20_000)

    def test_batch_size_zero_rejected(self):
        with pytest.raises(UsageError, match="batch-size"):
            check_batch_size(0, 20_000)

    def test_run_limit_zero_disables_check(self):
        """max_items_per_run=0 = 关闭护栏（#358 语义），批大小不拦。"""
        check_batch_size(99_999, 0)

    def test_watermark_below_batch_size_allowed(self):
        """低水位线 + 大批次合法（codex #531 P2-1）。

        水位线是补货触发阈值（水位 < 水位线即投下一批），不是容量承诺：
        --watermark=100 --batch-size=5000 且当前水位 0 时 0 < 100 照常投
        第一批，「水位低于批大小就永远补不进一批」不成立——启动期不拒绝。
        """
        check_watermark(100)

    def test_watermark_zero_rejected(self):
        with pytest.raises(UsageError, match="watermark"):
            check_watermark(0)
