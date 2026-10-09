/* #633 冲突生命周期与保存编排（kimi/codex review 拆出）：状态机
   （draftSaveController）的状态转换纯函数、调度决策与 PUT 响应分派。
   计时器/CAS 基线/请求生命期留在 controller（它们与竞争时序强耦合），
   这里是可以独立测试的决策逻辑。 */

import type { DraftSaveState } from './draftSaveTypes'

/* 采用服务端版本（adoptServerDraft/hydrate 共用）：回到 idle、清除冲突，
   savedAt 跟随服务端时间戳（无时间戳时保留原值）。#1143：savedHash 同
   步到服务端草稿身份（无 hash 时保留原值——旧服务端/不可解析草稿）。 */
export function conflictResolvedState(
  current: DraftSaveState,
  savedAt: string | null | undefined,
  savedHash?: string | null
): DraftSaveState {
  return {
    ...current,
    status: 'idle',
    savedAt: savedAt ?? current.savedAt,
    savedHash: savedHash ?? current.savedHash,
    conflict: false,
    conflictDraftYaml: undefined,
    conflictDraftHash: undefined,
  }
}

/* 进入冲突态（409 响应 / turn-end 服务端前进共用）：error 常驻 + 服务端
   草稿暴露给 UI（采用入口），savedAt 显示服务端真值。#1143：serverHash
   存进 conflictDraftHash（不进 savedHash——冲突期间编辑器有未落盘编辑，
   发布以编辑器为准，草稿卡一致性提示应保留；采用服务端版本时才经
   adopt 恢复成 savedHash）。 */
export function conflictEnteredState(
  current: DraftSaveState,
  serverYaml: string | null,
  serverAt: string | null,
  serverHash?: string | null
): DraftSaveState {
  return {
    ...current,
    status: 'error',
    savedAt: serverAt ?? current.savedAt,
    conflict: true,
    conflictDraftYaml: serverYaml,
    conflictDraftHash: serverHash ?? undefined,
  }
}

/* 冲突解除（resolveConflict(keep-mine)/画布回退到已持久化值）：清冲突
   标记；#804 轮 6 H6：status 从 error（冲突态的占位，横幅语义靠
   conflict 标记而非 error 本身）收敛到 saved/idle——采用 Agent 版本成功
   后不该留假 error 态。keep-mine 随后的保存会推进 savedAt/status。
   #1143 评审 P3-5：conflictDraftHash 与 conflictDraftYaml 对称清空（与
   conflictResolvedState 一致；下次进冲突必然整体覆写，这里消除不对称
   陷阱）。 */
export function conflictClearedState(current: DraftSaveState): DraftSaveState {
  return {
    ...current,
    status: current.savedAt ? 'saved' : 'idle',
    conflict: false,
    conflictDraftYaml: undefined,
    conflictDraftHash: undefined,
  }
}

/* 冲突解除后挂起的 keep-mine 保存是否应发起：有 pending 内容才补发。 */
export function pendingAfterResolve(
  pendingSave: { yaml: string; requestId: number } | null,
  keepMine: boolean
): { yaml: string; requestId: number } | null {
  return keepMine ? pendingSave : null
}

/* 回退到已持久化值后的可见状态：pending/error 收回 saved/idle；冲突态
   （画布回到服务端一致内容）一并清标记（kimi P2-3）。 */
export function revertedState(current: DraftSaveState): DraftSaveState {
  const status = current.savedAt ? 'saved' : 'idle'
  if (current.conflict) return conflictClearedState({ ...current, status })
  if (current.status === 'pending' || current.status === 'error') {
    return { ...current, status }
  }
  return current
}

/* schedule 的调度决策（kimi P1-2/P2-4）：空白内容 → skip；回退到已持久化
   值且无在途 → revert；否则进入 pending（conflict 态挂起，不 arm 计时器）。 */
export type ScheduleDecision =
  | { action: 'skip' }
  | { action: 'revert' }
  | { action: 'pending'; armTimer: boolean }

export function decideSchedule(
  yaml: string,
  persisted: boolean,
  inFlight: boolean,
  inConflict: boolean
): ScheduleDecision {
  if (!yaml.trim()) return { action: 'skip' }
  if (persisted && !inFlight) return { action: 'revert' }
  return { action: 'pending', armTimer: !inConflict }
}

/* 清一个可空计时器（controller 的 timer/retryTimer 同型）。 */
export function stopTimer(
  timer: ReturnType<typeof setTimeout> | null
): ReturnType<typeof setTimeout> | null {
  if (timer !== null) clearTimeout(timer)
  return null
}

/* 成功响应回调：作废与否都推进基线（R2 P1）；current 时才更新可见状态。
   #1143：savedHash 是本次落盘内容的服务端语义身份（响应带回，旧服务端
   或不可解析草稿为 null——调用方按「无法核对」降级）。 */
export type OnSaveSuccess = (
  yaml: string,
  updatedAt: string | null,
  current: boolean,
  savedHash: string | null
) => void

/* 失败回调：409 冲突（WorkflowDraftConflictError，携带 current_draft）
   进入冲突态（resolve(false)）；其它失败 error + 退避重试。 */
export type OnSaveFailure = (
  error: unknown,
  resolve: (ok: boolean) => void
) => void

/* 保存编排：发起 PUT 并按响应分派回调。requestId 过期的迟到响应只推进
   基线、不构成失败信号（R2 P1）。 */
export function runSave(options: {
  put: (
    yaml: string,
    keepalive: boolean,
    expectedAt: string
  ) => Promise<{ updated_at?: string | null; definition_hash?: string | null }>
  yaml: string
  keepalive: boolean
  expectedAt: string
  requestId: number
  isCurrentRequest: (requestId: number) => boolean
  clearInFlight: (requestId: number) => void
  onSuccess: OnSaveSuccess
  onFailure: OnSaveFailure
  resolve: (ok: boolean) => void
}): void {
  const { put, yaml, keepalive, expectedAt, requestId } = options
  put(yaml, keepalive, expectedAt)
    .then((response) => {
      // onSuccess BEFORE clearInFlight: the success handler advances the
      // persisted baseline (lastPersistedAt), and a queued save drains on
      // clearInFlight — the drain must re-read the ADVANCED baseline, not
      // the pre-A snapshot (codex R3 P1 serialization).
      options.onSuccess(
        yaml,
        response.updated_at ?? null,
        options.isCurrentRequest(requestId),
        response.definition_hash ?? null
      )
      options.clearInFlight(requestId)
      options.resolve(true)
    })
    .catch((error: unknown) => {
      options.clearInFlight(requestId)
      if (!options.isCurrentRequest(requestId)) return options.resolve(true)
      options.onFailure(error, options.resolve)
    })
}

/* beforeunload 护栏谓词：pending（未落盘）/在途写入/失败未恢复/冲突挂起
   （本页编辑未保存且自动 flush 已停）都算未保存。 */
export function hasPendingWork(
  hasPending: boolean,
  inFlight: boolean,
  errored: boolean,
  inConflict: boolean
): boolean {
  return hasPending || inFlight || errored || inConflict
}

/* kimi review P1-2 冲突出口的签名（persistence 与 serverSync 共享）。
   #1143：serverHash（可选）是被采用服务端草稿的语义身份。 */
export type AdoptServerDraft = (
  serverYaml: string,
  serverAt: string | null,
  onAdopt?: (serverYaml: string) => void,
  serverHash?: string | null
) => void
export type ResolveConflict = (keepMine: boolean) => void
