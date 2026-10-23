"""#1331: nightly failure-tracker 必须 needs 全部 lane 才能在 schedule 失败时触发。

run 39195751594：exemption-expiry 与 deps-audit 失败，failure-tracker 却显示
skipped——job 没有 needs，`failure()` 没有可观察的祖先结果，永远为假。修复 =
needs 全部 lane + `always() && ... && contains(needs.*.result, 'failure')`；
调度成功时不误报、dispatch 不开 issue、按标题去重只维护一个 ops issue。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]


def _failure_tracker() -> tuple[dict, dict]:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/nightly-gate.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    return jobs, jobs["failure-tracker"]


def test_failure_tracker_needs_every_nightly_lane() -> None:
    """needs 是触发前提：没有依赖关系的 job 观察不到任何 lane 失败。
    新增 nightly lane 时必须同步进 needs——本测试按全集对齐强制这一点。"""
    jobs, tracker = _failure_tracker()

    lanes = {name for name in jobs if name != "failure-tracker"}
    assert set(tracker["needs"]) == lanes


def test_failure_tracker_triggers_on_schedule_failure_only() -> None:
    _, tracker = _failure_tracker()

    condition = str(tracker["if"])
    # always()：任一 need 失败时 GitHub 默认 skip 后续 job，必须显式存活。
    assert "always()" in condition
    # 只对 schedule 失败开/更新 issue；dispatch 失败属于发起者自己。
    assert "github.event_name == 'schedule'" in condition
    # 触发判据落在 needs 结果上（无 needs 时 failure() 永远为假，#1331）。
    assert "needs.*.result" in condition
    assert "failure" in condition


def test_failure_tracker_dedups_one_ops_issue_by_title() -> None:
    """成功时不误报（上面的 if 管住）；持续红时按标题去重——已有 open issue
    则评论而非新开，永远只维护一个 ops issue。"""
    _, tracker = _failure_tracker()

    body = "\n".join(str(step.get("run", "")) for step in tracker["steps"])
    assert 'gh issue list --state open --search "in:title' in body
    assert "gh issue comment" in body
    assert "--label ops" in body


def test_failure_tracker_triggers_on_cancelled_lanes() -> None:
    """r1 P2-1：超时 lane 的结论是 cancelled 而非 failure（四条 lane 都带
    timeout-minutes，本仓库有 stall 病史——FLAKY-004 浏览器下载挂死 41 分钟）。
    触发条件必须同时覆盖 cancelled，否则「weekly 红无人知」从超时入口复发。"""
    _, tracker = _failure_tracker()

    condition = str(tracker["if"])
    assert "contains(needs.*.result, 'failure')" in condition
    assert "contains(needs.*.result, 'cancelled')" in condition
