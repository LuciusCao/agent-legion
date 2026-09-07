"""Cold-start ramp-up state machine tests (worker/ramp_up.py, issue #471).

The ramp throttles the *release rate* on cold start: effective capacity
starts at ``initial``, rises by ``step`` every ``interval_seconds`` of
claiming time, and hands control back to the normal capacity semantics
once the target is reached. These tests pin:

- the ladder itself (64→128→…→640 form, each tier held ~interval);
- claim_enabled pauses folding back out (a paused worker does not burn
  its ramp);
- "only up" during the window + hot-reload keeping progress;
- completion permanence and the disabled pass-through (target returned
  unchanged — today's behavior);
- config validation boundaries (fail-fast with field-naming errors);
- the executor wiring surface: ramp_pass / apply_ramp_hot_reload /
  slots_line_suffix.

The clock is the caller-fed ``now`` argument — deterministic, no sleeps.
"""

from __future__ import annotations

import pytest

from worker.ramp_up import (
    RampUpControls,
    RampUpSnapshot,
    RampUpState,
    apply_ramp_hot_reload,
    load_ramp_up_controls,
    normalized_ramp_up_block,
    ramp_pass,
    slots_line_suffix,
    validate_ramp_up,
)

pytestmark = pytest.mark.no_db

# issue #471 的示例参数：64 / 64 / 120s —— 20 分钟从 64 爬到 640。
EXAMPLE = RampUpControls(enabled=True, initial=64, step=64, interval_seconds=120)


def _ladder(
    controls: RampUpControls = EXAMPLE,
    target: int = 640,
    *,
    step_seconds: float = 30,
    passes: int = 44,
) -> list[RampUpSnapshot]:
    """Drive the state machine with an injected clock; one observe per pass.

    窗口关闭后的 pass 返回 None（直通目标）——这些 pass 不进结果列表，
    到顶后的直通语义由 test_completed_window_stays_closed 单独钉。"""
    ramp = RampUpState(controls=controls)
    paused: float | None = None
    views: list[RampUpSnapshot] = []
    now = 0.0
    for _ in range(passes):
        now += step_seconds
        view, paused = ramp_pass(ramp, paused, target, now, claim_enabled=True)
        if view is None:
            break
        views.append(view)
    return views


def test_ladder_rises_in_steps_and_holds_each_tier() -> None:
    # 每 120s 升一档（64 的倍数），档内保持——30s 采样下每档出现 4 次。
    tiers = [view.effective for view in _ladder()]
    assert tiers[:8] == [64, 64, 64, 64, 128, 128, 128, 128]
    assert tiers[16:20] == [320, 320, 320, 320]


def test_ladder_reaches_target_then_stops_growing() -> None:
    # 到顶后不再增长：640 封顶，窗口关闭（active=False），视图带 completed。
    views = _ladder(target=640)
    assert views[-1].effective == 640
    assert views[-1].completed
    assert all(view.effective <= 640 for view in views)


def test_first_observe_is_the_initial_tier_regardless_of_gap() -> None:
    # 首次 observe：初始档位生效（之前的时间不燃烧——窗口从第一次领取起算）。
    ramp = RampUpState(controls=EXAMPLE)
    view, _ = ramp_pass(ramp, None, 640, 10_000.0, claim_enabled=True)
    assert view is not None and view.effective == 64


def test_next_tier_seconds_counts_down_within_tier() -> None:
    # 首个 pass 建立时钟基准（不折入 elapsed）：next=interval；随后逐 pass
    # 递减。到点升档后从新 interval 重新起算。
    views = _ladder(step_seconds=30, passes=5)
    assert views[0].next_tier_seconds == pytest.approx(120.0)
    assert views[1].next_tier_seconds == pytest.approx(90.0)
    assert views[2].next_tier_seconds == pytest.approx(60.0)
    assert views[3].next_tier_seconds == pytest.approx(30.0)
    assert views[4].effective == 128
    assert views[4].next_tier_seconds == pytest.approx(120.0)


def test_pause_does_not_burn_the_ramp() -> None:
    # 暂停（claim_enabled=false）窗口：档位保持、不推进；恢复后暂停墙钟
    # 被折回（elapsed − pause_span），下一档等待只算活跃时长。
    ramp = RampUpState(controls=EXAMPLE)
    paused: float | None = None
    view, paused = ramp_pass(ramp, paused, 640, 30.0, claim_enabled=True)
    assert view is not None and view.effective == 64  # 首 pass 建立时钟基准
    view, paused = ramp_pass(ramp, paused, 640, 60.0, claim_enabled=True)
    assert view is not None and view.next_tier_seconds == pytest.approx(90.0)  # 30s 活跃
    view, paused = ramp_pass(ramp, paused, 640, 360.0, claim_enabled=False)  # 暂停 300s
    assert view is not None and view.effective == 64  # 暂停中档位不闪断
    assert paused == 360.0
    view, paused = ramp_pass(ramp, paused, 640, 660.0, claim_enabled=True)
    assert view is not None and view.effective == 64  # 300s 暂停被折回
    assert view.next_tier_seconds == pytest.approx(90.0)  # 只算活跃的 30s
    view, paused = ramp_pass(ramp, paused, 640, 690.0, claim_enabled=True)
    assert view is not None and view.effective == 64  # 活跃 60s，仍在首档
    view, paused = ramp_pass(ramp, paused, 640, 750.0, claim_enabled=True)
    assert view is not None and view.effective == 128  # 累计活跃 120s → 升档


def test_paused_before_first_observe_reports_zero_tier() -> None:
    """#493 P3-2：首观察前暂停（启用爬坡但从未领取过）快照是 0 档——控制台
    对 effective=0 隐藏爬坡行（app.js rampUpLine），不显示「0/640」噪音。"""
    ramp = RampUpState(controls=EXAMPLE)
    view, paused = ramp_pass(ramp, None, 640, 10.0, claim_enabled=False)
    assert view is not None and view.effective == 0 and view.target == 640
    assert view.next_tier_seconds is None
    assert ramp.active is True  # 窗口未关：首次领取才起步


def test_target_resize_mid_ramp_clamps_to_new_target() -> None:
    # 目标热缩（max_concurrency 调小）：生效容量立即被新目标钳住。
    views = _ladder(EXAMPLE, target=128)
    assert all(view.effective <= 128 for view in views)
    assert views[-1].completed


def test_only_up_during_window_smaller_initial_does_not_pull_back() -> None:
    """#493 P2-2：窗口内热更到更小的 initial/step 不把在途档位拉回。

    reviewer 复现形态：64/64 爬到 192 后热更 initial=1/step=1——未修复的
    实现下一 pass 算出 1+240×1=241？不：interval 同时变 1s 时 tiers 只按
    新 interval 计（240/1=240 档）反而虚高；关键是 initial/step 远小于
    在途档位且 tiers 不大的组合（如 interval=600 使 tiers=0）会算出
    1+0=1，直接回撤到 1。这里用能暴露回撤的参数钉住三处容量面共享的
    effective 恒不下降。"""
    ramp = RampUpState(controls=EXAMPLE)
    paused: float | None = None
    view, paused = ramp_pass(ramp, paused, 640, 120.0, claim_enabled=True)  # 基准
    view, paused = ramp_pass(ramp, paused, 640, 360.0, claim_enabled=True)  # 活跃 240s
    assert view is not None and view.effective == 192
    # 热更 1/1/600：按新参数计算 1+0×1=1（远低于在途 192）——不得回撤。
    reloaded = apply_ramp_hot_reload(
        ramp, RampUpControls(enabled=True, initial=1, step=1, interval_seconds=600), print
    )
    assert reloaded is ramp
    view, paused = ramp_pass(ramp, paused, 640, 420.0, claim_enabled=True)
    assert view is not None and view.effective == 192, "热更更小参数不得回撤在途档位"
    # 新参数从当前档位起按新节奏继续：1/1/600 下计算档位要 191 档才追上
    # 在途 192——中短期内档位保持 192（floor 兜底），不会以旧节奏虚涨。
    for now in (1200.0, 3600.0):
        view, paused = ramp_pass(ramp, paused, 640, now, claim_enabled=True)
        assert view is not None and view.effective == 192, "新节奏生效：档位不虚涨也不回撤"


def test_completed_window_stays_closed() -> None:
    # 到顶 = 窗口永久关闭：后续 pass 的生效容量恒等于目标（正常语义）。
    ramp = RampUpState(controls=RampUpControls(enabled=True, initial=4, step=4, interval_seconds=1))
    paused: float | None = None
    view, paused = ramp_pass(ramp, paused, 8, 1.0, claim_enabled=True)  # 基准
    assert view is not None and view.effective == 4
    view, paused = ramp_pass(ramp, paused, 8, 2.0, claim_enabled=True)  # 活跃 1s → 到顶
    assert view is not None and view.completed
    assert ramp.active is False
    view, paused = ramp_pass(ramp, paused, 8, 100.0, claim_enabled=True)
    assert view is None  # 窗口关闭后 ramp_pass 直通（预算用目标）


def test_disabled_config_is_full_passthrough() -> None:
    # 禁用（无 ramp_up 块）：ramp_pass(None, ...) 返回 None——executor 的
    # 预算直接用 max_concurrency，行为与现状完全一致。
    assert validate_ramp_up(None).enabled is False
    assert validate_ramp_up({}) is not None  # 空对象 = 启用默认参数（非禁用）
    view, paused = ramp_pass(None, None, 640, 1.0, claim_enabled=True)
    assert view is None and paused is None


def test_disable_hot_reload_closes_window_and_reenable_reopens() -> None:
    # 热更置 null：立即关窗（目标直通）；重新启用：新窗口从头爬。
    ramp = apply_ramp_hot_reload(None, EXAMPLE, print)
    paused: float | None = None
    view, paused = ramp_pass(ramp, paused, 640, 120.0, claim_enabled=True)  # 基准
    view, paused = ramp_pass(ramp, paused, 640, 360.0, claim_enabled=True)  # 活跃 240s
    assert view is not None and view.effective == 192
    ramp = apply_ramp_hot_reload(ramp, RampUpControls(enabled=False), print)
    view, paused = ramp_pass(ramp, paused, 640, 361.0, claim_enabled=True)
    assert view is None
    ramp = apply_ramp_hot_reload(ramp, EXAMPLE, print)
    assert ramp.active is True
    view, paused = ramp_pass(ramp, paused, 640, 362.0, claim_enabled=True)
    assert view is not None and view.effective == 64  # 新窗口从初始档起步


def test_tier_change_logging_is_change_only_and_slots_style() -> None:
    lines: list[str] = []
    ramp = RampUpState(controls=EXAMPLE, log=lines.append)
    paused: float | None = None
    for now in range(30, 1231, 30):  # 活跃 1200s = 10 档，覆盖到顶
        view, paused = ramp_pass(ramp, paused, 640, float(now), claim_enabled=True)
        if view is None:  # 到顶后窗口关闭，直通
            break
    # 每档一条 + 到顶一条；同档重复 observe 不重记。
    assert [line.split()[2] for line in lines[:-1]] == [
        "64/640",
        "128/640",
        "192/640",
        "256/640",
        "320/640",
        "384/640",
        "448/640",
        "512/640",
        "576/640",
    ]
    assert lines[-1] == "worker ramp-up complete 640/640"
    assert lines[0] == "worker ramp-up 64/640 (step 64, next in 120s)"


def test_slots_line_suffix_matches_worker_slots_style() -> None:
    # 爬坡中：", ramp-up 64/640 (+Ns)"；禁用/到顶：空串（行与 #471 前一致）。
    views = _ladder(passes=2)
    assert slots_line_suffix(views[0]) == ", ramp-up 64/640 (+120s)"  # 首 pass 基准
    assert slots_line_suffix(views[1]) == ", ramp-up 64/640 (+90s)"
    assert slots_line_suffix(None) == ""
    done = RampUpSnapshot(640, 640, None)
    assert slots_line_suffix(done) == ""


def test_initial_at_or_above_target_completes_immediately() -> None:
    # initial ≥ 目标：首 pass 即到顶（为大档位写的配置不 brick 调小的 worker）。
    controls = RampUpControls(enabled=True, initial=640, step=64, interval_seconds=120)
    views = _ladder(controls, target=640, passes=2)
    assert views[0].completed and views[0].effective == 640


# ---- 配置校验边界（fail-fast 带指引）----


def test_validate_ramp_up_accepts_the_issue_example() -> None:
    controls = validate_ramp_up({"initial": 64, "step": 64, "interval_seconds": 120})
    assert controls == RampUpControls(True, 64, 64, 120.0)


def test_validate_ramp_up_defaults_and_none_disable() -> None:
    # 键缺省 = 1/1/60；None/False = 禁用（enabled=False）。
    assert validate_ramp_up({}) == RampUpControls(True, 1, 1, 60.0)
    assert validate_ramp_up(None).enabled is False
    assert validate_ramp_up(False).enabled is False


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"initial": 0}, "ramp_up.initial"),
        ({"initial": 1025}, "ramp_up.initial"),
        ({"initial": True}, "ramp_up.initial"),
        ({"initial": 1.5}, "ramp_up.initial"),
        ({"step": 0}, "ramp_up.step"),
        ({"step": "8"}, "ramp_up.step"),
        ({"interval_seconds": 0}, "ramp_up.interval_seconds"),
        ({"interval_seconds": 3601}, "ramp_up.interval_seconds"),
        ({"interval_seconds": True}, "ramp_up.interval_seconds"),
        ("not-a-mapping", "ramp_up 必须是对象"),
        ([1, 2], "ramp_up 必须是对象"),
    ],
)
def test_validate_ramp_up_rejects_invalid_blocks(block: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_ramp_up(block)


def test_validate_ramp_up_accepts_interval_as_int() -> None:
    # yaml 里整数字面量：int 也合法（归一为 float）。
    assert validate_ramp_up({"interval_seconds": 120}).interval_seconds == 120.0


def test_normalized_ramp_up_block_is_self_contained() -> None:
    # 落盘块键全量补齐（读回不依赖隐式默认）；禁用为 None。
    controls = validate_ramp_up({"initial": 64})
    assert normalized_ramp_up_block(controls) == {
        "initial": 64,
        "step": 1,
        "interval_seconds": 60.0,
    }
    assert normalized_ramp_up_block(validate_ramp_up(None)) is None


def test_load_ramp_up_controls_reads_yaml_block(tmp_path, monkeypatch) -> None:
    # 热读入口：yaml 原块 → 校验后的 controls（executor 每轮调用）。
    from pathlib import Path

    config = tmp_path / "worker.yaml"
    config.write_text(
        "max_concurrency: 640\nramp_up:\n  initial: 64\n  step: 64\n  interval_seconds: 120\n",
        encoding="utf-8",
    )
    assert load_ramp_up_controls(Path(config)) == RampUpControls(True, 64, 64, 120.0)
    config.write_text("max_concurrency: 640\n", encoding="utf-8")
    assert load_ramp_up_controls(Path(config)).enabled is False


def test_config_validation_round_trips_ramp_up() -> None:
    # validate_config（config_validation.py）出口：块归一化、None 保留。
    from worker.config_validation import validate_config

    base = {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "max_concurrency": 640,
    }
    assert validate_config(base)["ramp_up"] is None
    assert validate_config({**base, "ramp_up": {"initial": 64, "step": 64}})["ramp_up"] == {
        "initial": 64,
        "step": 64,
        "interval_seconds": 60.0,
    }
    with pytest.raises(ValueError, match="ramp_up.initial"):
        validate_config({**base, "ramp_up": {"initial": 0}})


def test_status_reporter_publishes_ramp_up_view(tmp_path) -> None:
    # 执行进程 → 状态文件 → 控制台链路：set_ramp_up 写进 current_executions
    # 的 ramp_up 键；无变化不重写（防抖），不在爬坡 = None（控制台隐藏行）。

    from worker.status import ExecutionStatusReporter, read_runtime_status

    path = tmp_path / "current_executions.json"
    reporter = ExecutionStatusReporter(path)
    reporter.set_ramp_up(RampUpSnapshot(64, 640, 90.0), 640)
    runtime = read_runtime_status(path)
    assert runtime["ramp_up"] == {"effective": 64, "target": 640, "next_tier_seconds": 90.0}
    # 停领/到顶/禁用（view=None）：状态文件记 None——控制台据此隐藏爬坡行。
    reporter.set_ramp_up(None, 640)
    assert read_runtime_status(path)["ramp_up"] is None
