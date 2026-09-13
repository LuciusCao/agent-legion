/* #633 冲突生命周期与保存编排（kimi/codex review 拆出）：状态机
   （draftSaveController）的状态转换纯函数、调度决策与 PUT 响应分派。
   计时器/CAS 基线/请求生命期留在 controller（它们与竞争时序强耦合），
   这里是可以独立测试的决策逻辑。 */

import type { DraftSaveState } from './draftSaveTypes'

/* 采用服务端版本（adoptServerDraft/hydrate 共用）：回到 idle、清除冲突，
   savedAt 跟随服务端时间戳（无时间戳时保留原值）。 */
export function conflictResolvedState(
  current: DraftSaveState,
  savedAt: string | null | undefined
): DraftSaveState {
  return {
    ...current,
    status: 'idle',
    savedAt: savedAt ?? current.savedAt,
    conflict: false,
    conflictDraftYaml: undefined,
  }
}

/* 进入冲突态（409 响应 / turn-end 服务端前进共用）：error 常驻 + 服务端
   草稿暴露给 UI（采用入口），savedAt 显示服务端真值。 */
export function conflictEnteredState(
  current: DraftSaveState,
  serverYaml: string | null,
  serverAt: string | null
): DraftSaveState {
  return {
    ...current,
    status: 'error',
    savedAt: serverAt ?? current.savedAt,
    conflict: true,
    conflictDraftYaml: serverYaml,
  }
}

/* 冲突解除（resolveConflict(keep-mine)/画布回退到已持久化值）：仅清
   冲突标记，状态与 savedAt 保持不变（keep-mine 的保存会推进它们）。 */
export function conflictClearedState(current: DraftSaveState): DraftSaveState {
  return { ...current, conflict: false, conflictDraftYaml: undefined }
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

/* 成功响应回调：作废与否都推进基线（R2 P1）；current 时才更新可见状态。 */
export type OnSaveSuccess = (
  yaml: string,
  updatedAt: string | null,
  current: boolean
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
  ) => Promise<{ updated_at?: string | null }>
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
        options.isCurrentRequest(requestId)
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

/* kimi review P1-2 冲突出口的签名（persistence 与 serverSync 共享）。 */
export type AdoptServerDraft = (
  serverYaml: string,
  serverAt: string | null,
  onAdopt?: (serverYaml: string) => void
) => void
export type ResolveConflict = (keepMine: boolean) => void
