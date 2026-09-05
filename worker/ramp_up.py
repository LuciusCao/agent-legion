"""Cold-start capacity ramp-up for the Worker supervisor (issue #471).

Cold start (worker start / ``claim_enabled`` false→true) releases a queued
backlog all at once: hundreds of agents fire their first LLM requests in
the same instant and the transient burst saturates the provider. The
recovery-period "slow climb" that exists today is only the claim loop's
physical speed — uncontrolled, unconfigurable, and incidentally protective
(it evaporates as #448/#472 make claiming faster).

This module throttles the **release rate**, not the claim rate:

- pacing (#472) owns "how long to wait between claim passes" — this state
  machine owns "how many claims this pass may take" (the claim budget's
  capacity input). The two are orthogonal and must stay decoupled: a fast
  claim line under a small effective capacity still admits at most
  ``effective`` concurrent executions.
- the ramp clamps only the *new-claim budget*: executions already running
  keep running; completions during the ramp leave the freed slots
  refillable up to the current tier ("only up" — the effective capacity
  never decreases inside a ramp window, so the completion flow cannot
  couple back into oscillation).
- disabled (no ``ramp_up`` config block) is a no-op: the wiring passes the
  target straight through — exactly today's behavior, pinned by tests.

Mechanics: the effective capacity starts at ``initial`` and rises by
``step`` every ``interval_seconds`` of *claiming time* — a virtual clock
that only advances while ``claim_enabled`` is on (pause gaps are folded
back out via ``deduct``). The clock is injectable via ``observe(now)`` for
deterministic tests (pattern of ``claim_pacing.py``). Reaching the target
closes the window permanently; afterwards the normal capacity semantics
(hot-read ``max_concurrency``, completion/refill churn) apply unchanged.
Tier changes log through the caller-supplied ``log`` callable, one line
per change, aligned with the existing ``worker slots`` style.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY, load_config


@dataclass(frozen=True)
class RampUpControls:
    """Hot-reloaded ramp parameters; ``enabled`` False = 直通模式（禁用）。"""

    enabled: bool = False
    initial: int = 1
    step: int = 1
    interval_seconds: float = 60.0


@dataclass(frozen=True)
class RampUpSnapshot:
    """One observation of the ramp for logs and the status file."""

    effective: int
    target: int
    # Remaining seconds at the current tier (None once the ramp completes).
    next_tier_seconds: float | None

    @property
    def completed(self) -> bool:
        return self.effective >= self.target

    @property
    def next_tier_seconds_rounded(self) -> float:
        """Remaining seconds at display precision (whole s, floored at 0)."""
        value = self.next_tier_seconds
        return 0.0 if value is None else max(0.0, float(round(value)))


def _require_int(block: dict[str, Any], key: str, default: int) -> int:
    """Fetch one ramp_up integer field in [1, MAX_DYNAMIC_CONCURRENCY]."""
    value = block.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"ramp_up.{key} 必须是 1 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    return value


def validate_ramp_up(block: Any) -> RampUpControls:
    """Validate one raw ``ramp_up`` config block into RampUpControls.

    ``None``/``False`` = 禁用（回到现状的一次性全量）。mapping 需整数
    ``initial``/``step`` ∈ [1,1024]、``interval_seconds`` ∈ [0.2,3600]
    （键缺省 1/1/60），否则 ``ValueError`` 点名键。``initial`` 超过当前
    max_concurrency 不报错——首 observe 即到顶（为大档位写的配置不该把
    中途调小的 worker 变砖）。"""
    if block is None or block is False:
        return RampUpControls(enabled=False)
    if not isinstance(block, dict):
        raise ValueError("ramp_up 必须是对象（initial/step/interval_seconds）或留空禁用")
    initial = _require_int(block, "initial", 1)
    step = _require_int(block, "step", 1)
    interval = block.get("interval_seconds", 60)
    bad_interval = (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not 0.2 <= float(interval) <= 3600
    )
    if bad_interval:
        raise ValueError("ramp_up.interval_seconds 必须在 0.2 到 3600 秒之间")
    return RampUpControls(True, initial, step, float(interval))


def load_ramp_up_controls(path: Any) -> RampUpControls:
    """Hot-reload the ramp_up block from the worker config (each loop pass)."""
    return validate_ramp_up(load_config(path).get("ramp_up"))


def normalized_ramp_up_block(controls: RampUpControls) -> dict[str, Any] | None:
    """RampUpControls → 落盘/回显配置块（None = 禁用）；键全量补齐、
    interval 归一 float——块自包含，读回不依赖隐式默认。"""
    if not controls.enabled:
        return None
    return {
        "initial": controls.initial,
        "step": controls.step,
        "interval_seconds": controls.interval_seconds,
    }


class RampUpState:
    """Cold-start capacity ramp state machine (single entry point:
    ``observe(target, now, claim_enabled)`` → ``RampUpSnapshot``).

    ``now`` is the caller's monotonic clock reading (injectable for
    deterministic tests); consecutive observes fold their delta into the
    *virtual claiming clock* only while claiming is enabled — pause gaps do
    not burn the ramp (``ramp_pass`` folds them back out on resume)."""

    def __init__(
        self, *, controls: RampUpControls, log: Callable[[str], None] | None = None
    ) -> None:
        self._controls = controls
        self._log = log
        self._last_observed: float | None = None
        self._claim_clock = 0.0
        self._active = controls.enabled
        self._reported: int | None = None
        self._completed_logged = False

    @property
    def active(self) -> bool:
        """True while the ramp window is open (config enabled and incomplete)."""
        return self._active

    def reconfigure(self, controls: RampUpControls) -> None:
        """Hot-reload parameters without resetting the ramp position.

        A running ramp keeps its virtual clock; a smaller new ``initial``
        cannot pull an in-flight tier back down (#471: 爬坡期只升不降)。
        Disabling ends the window immediately; re-enabling after a closed
        window opens a fresh one（新窗口从头爬：操作员重开爬坡的意图就
        是要节流，而不是继承旧进度直通）。"""
        if controls.enabled and not self._active and not self._controls.enabled:
            self._claim_clock = 0.0
            self._last_observed = None
            self._reported = None
            self._completed_logged = False
            self._active = True
        elif not controls.enabled:
            self._active = False
        self._controls = controls

    def observe(
        self, target: int, now: float, *, claim_enabled: bool, pause_seconds: float = 0.0
    ) -> RampUpSnapshot:
        """One claim pass: fold elapsed time, return the effective capacity.

        ``target`` is the current hot-read ``max_concurrency``. Paused passes
        hold the tier (the caller anchors the resume); once the effective
        capacity reaches the target the window closes. ``pause_seconds``
        subtracts a claiming-pause span from this pass's elapsed time (the
        executor's resume pass passes ``now - paused_since``)."""
        if self._last_observed is not None and claim_enabled:
            elapsed = max(0.0, now - self._last_observed - max(pause_seconds, 0.0))
            self._claim_clock += elapsed
        self._last_observed = now
        if not claim_enabled or not self._controls.enabled:
            # 暂停/禁用：不推进爬坡。暂停保持最后档位（控制台进度不闪断，
            # next_tier=None 提示不在计时）；禁用直通目标。
            if not self._controls.enabled:
                return RampUpSnapshot(target, target, None)
            return RampUpSnapshot(self._reported or 0, target, None)
        interval = self._controls.interval_seconds
        tiers = int(self._claim_clock // interval) if interval > 0 else 1 << 30
        effective = min(target, self._controls.initial + tiers * self._controls.step)
        if effective >= target:
            self._active = False
            if not self._completed_logged:
                self._completed_logged = True
                self._emit(f"worker ramp-up complete {target}/{target}")
            self._reported = target
            return RampUpSnapshot(target, target, None)
        remaining = interval - (self._claim_clock % interval)
        # 判变打日志：档位变化才记一行（对齐 worker slots 风格，零稳态噪音）。
        if effective != self._reported:
            self._reported = effective
            self._emit(
                f"worker ramp-up {effective}/{target}"
                f" (step {self._controls.step}, next in {remaining:.0f}s)"
            )
        return RampUpSnapshot(effective, target, remaining)

    def _emit(self, message: str) -> None:
        if self._log is not None:
            self._log(message)


def ramp_pass(
    state: RampUpState | None,
    paused_since: float | None,
    target: int,
    now: float,
    claim_enabled: bool,
) -> tuple[RampUpSnapshot | None, float | None]:
    """The executor's per-pass ramp policy (#471) → ``(view, paused_since)``.

    view=None（无状态/窗口已关）→ 预算直通用目标；暂停 pass 只记录暂停
    起点，恢复 pass 把暂停跨度折出 elapsed——停领墙钟不燃烧爬坡。"""
    if state is None or not state.active:
        return None, paused_since
    if not claim_enabled:
        stamp = now if paused_since is None else paused_since
        return state.observe(target, now, claim_enabled=False), stamp
    # 恢复领取：本轮 elapsed 折掉暂停跨度（now - paused_since），暂停墙钟
    # 不进虚拟时钟；此后各 pass 继续全量折入。
    pause = 0.0 if paused_since is None else now - paused_since
    return state.observe(target, now, claim_enabled=True, pause_seconds=pause), None


def apply_ramp_hot_reload(
    state: RampUpState | None, controls: RampUpControls, log: Callable[[str], None]
) -> RampUpState | None:
    """Hot-reload entry: reconfigure an open window; disabled→enabled flip
    mid-run opens a fresh window（executor 建状态也走这里，单点收口）."""
    if state is not None:
        state.reconfigure(controls)
        return state
    if not controls.enabled:
        return None
    return RampUpState(controls=controls, log=log)


def slots_line_suffix(view: RampUpSnapshot | None) -> str:
    """The ``worker slots`` log line's ramp-up suffix (#471): in-progress →
    ``, ramp-up <effective>/<target> (+<n>s)``；其余（禁用/到顶/未观察）为空
    ——无爬坡时日志行与 #471 前逐字节一致。"""
    if view is None or view.completed:
        return ""
    return f", ramp-up {view.effective}/{view.target} (+{view.next_tier_seconds_rounded:.0f}s)"


def slots_line(
    active: int, target: int, code_target: int, depth: int, view: RampUpSnapshot | None
) -> str:
    """The executor's whole ``worker slots`` heartbeat line（含 #471 爬坡后缀）。"""
    return (
        f"worker slots {active}/{target}+{code_target},"
        f" upload queue depth {depth}{slots_line_suffix(view)}"
    )
