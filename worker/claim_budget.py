"""Per-pool claim-pass budget (#471 ramp gate × #352 pools × #534 inhibition).

Split out of ``worker/executor.py`` (file-budget ceiling): executor keeps the
claim loop, this module owns how the two pools' budgets derive from effective
capacity, active executions, upload backpressure, and the #534 cross-pool
deferral. ``pass_budget`` mutates its ``pool_deferred`` argument (liftoff of
a suppressed pool when its unsuppressed avail turns positive) — the executor
passes the same set on every pass and is the only writer of ``add``; the
release side lives here, next to the budget math that defines it.
"""

from __future__ import annotations

from concurrent.futures import Future

from worker.transfer_controls import claim_availability


def pass_budget(
    active: set[Future[None]],
    active_kinds: dict[Future[None], str],
    *,
    effective: int,
    targets: tuple[int, int],
    claim_enabled: bool,
    pool_deferred: set[str],
    upload_depth: int,
    backlog: int | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """One claim pass's per-pool budget + capacity declaration (#534).

    agent/code 各自按「生效容量 - 活跃数」起算（claim_enabled 关闭即 0），
    再过上传背压衰减（claim_availability）。返回 ``(budget, declared)``：

    - budget：本 pass 各池可领数量。#534 越池抑制——领到「本地预算已尽
      的池」的活后该池记入 pool_deferred，抑制期间预算直接视为 0。
    - declared：随 claim 调用向 Host 声明的各池容量。这是越池抑制唯一
      真正的止血通道：Host 按「active < 声明容量」分池发活（#501 声明
      的是目标容量，不随爬坡档位走），本地预算只能 break 单个 pass，
      不压声明的话 Host 每个 pass 都会再发一个，running 一路爬到声明
      容量，绕过 ramp-up/背压。抑制期间该池声明压到 ``min(活跃数,
      目标)``（Host 的门即关闭）；解除后回声目标容量。

    解除面 = 该池「未被抑制时的预算」转正（avail > 0：执行完成/档位
    推进/背压消退都会让 avail 回正）——比 base > 0 更严：背压把
    availability 钳 0 时 base 仍可 > 0，此时解除抑制会立刻再越池一个。
    """
    agent_target, code_target = targets
    agent_active = sum(1 for kind in active_kinds.values() if kind == "agent")
    agent_base = max(0, effective - agent_active) if claim_enabled else 0
    code_active = len(active) - agent_active
    code_base = max(0, code_target - code_active) if claim_enabled else 0
    agent_avail = claim_availability(agent_base, upload_depth, effective, backlog)
    code_avail = claim_availability(code_base, upload_depth, max(code_target, 1), backlog)
    if agent_avail > 0:
        pool_deferred.discard("agent")
    if code_avail > 0:
        pool_deferred.discard("code")
    agent_held = "agent" in pool_deferred
    code_held = "code" in pool_deferred
    # held ⟺ avail == 0（held 只在 avail=0 时成立），预算表达式数值上恒
    # 等于 avail——写 0 仅为语义显式化：抑制期间即使未来 avail 语义变化
    # （如热更瞬间 base 恢复）也不放开该池的本地预算。
    budget = {"agent": 0 if agent_held else agent_avail, "code": 0 if code_held else code_avail}
    declared = {
        "agent": min(agent_active, agent_target) if agent_held else agent_target,
        "code": min(code_active, code_target) if code_held else code_target,
    }
    return budget, declared
