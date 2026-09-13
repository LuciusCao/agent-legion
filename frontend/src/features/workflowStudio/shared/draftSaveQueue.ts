/* #633 codex review R3 P1：保存请求的排队/补发编排（纯逻辑，无计时器）。
   在途 PUT 未结束时，debounce 到期的下一次保存不得用同一旧 CAS 基线并发
   竞争（A 先落盘则 B 必然被自己的 A 409，连续编辑被误报为外部冲突）——B
   排队，A 终态后以 A 推进的新基线补发。一次只放行一个（补发本身成为新的
   在途）；排队请求被作废（requestId 过期）或冲突挂起时按既有语义处置。
   计时器与 CAS 基线留在 controller（与竞争时序强耦合），这里可独立测试。 */

import {
  DRAFT_NEVER_SAVED,
  WorkflowDraftConflictError,
} from '../../../api/workflowDraft'
import { runSave } from './draftSaveConflict'
import {
  MAX_PUT_RETRIES,
  RETRY_BASE_MS,
  type DraftSaveFlushResult,
} from './draftSaveTypes'

export type QueuedSave = { yaml: string; requestId: number }
type Waiter = (result: DraftSaveFlushResult) => void

/* 排队决策（drain 在 PUT 终态时调用）：
   - 无排队且无在途 → 丢弃等待方（保存已被后续编辑取代，与 flushNow 语义一致）
   - 有排队但在途未清零（重试链仍在跑）→ 留待下一次终态
   - 排队 requestId 过期 → 作废（ok：与 flushNow 的作废语义一致）
   - 冲突挂起 → 交还 controller 转 pendingSave（kimi P1-2 的挂起语义覆盖
     补发路径），等待方拿到 ok:false（发布守卫中止）
   - 否则 → reissue：controller 以发起时刻的基线补发 */
export type DrainDecision =
  | { action: 'none'; staleWaiters: Waiter[] }
  | { action: 'wait' }
  | { action: 'abort'; staleWaiters: Waiter[] }
  | { action: 'conflict'; queued: QueuedSave; staleWaiters: Waiter[] }
  | { action: 'reissue'; queued: QueuedSave; waiters: Waiter[] }

export class DraftSaveQueue {
  private queuedSave: QueuedSave | null = null
  private waiters: Waiter[] = []

  enqueue(save: QueuedSave, resolve: Waiter): void {
    this.queuedSave = save
    this.waiters.push(resolve)
  }

  get size(): number {
    return this.queuedSave === null ? 0 : 1
  }

  /* 取走当前排队与等待方（drain 决策基于取出后的快照）。 */
  take(): { queued: QueuedSave | null; waiters: Waiter[] } {
    const queued = this.queuedSave
    const waiters = this.waiters
    this.queuedSave = null
    this.waiters = []
    return { queued, waiters }
  }

  restore(queued: QueuedSave, waiters: Waiter[]): void {
    this.queuedSave = queued
    this.waiters = waiters
  }

  /* 作废一切排队（abortPending：递增 requestId 后调用），返回等待方。 */
  discard(): Waiter[] {
    const waiters = this.waiters
    this.queuedSave = null
    this.waiters = []
    return waiters
  }

  static decide(
    snapshot: { queued: QueuedSave | null; waiters: Waiter[] },
    inFlight: number,
    currentRequest: (requestId: number) => boolean,
    inConflict: boolean
  ): DrainDecision {
    const { queued, waiters } = snapshot
    if (queued === null) return { action: 'none', staleWaiters: waiters }
    if (inFlight !== 0) return { action: 'wait' }
    if (!currentRequest(queued.requestId))
      return { action: 'abort', staleWaiters: waiters }
    if (inConflict) return { action: 'conflict', queued, staleWaiters: waiters }
    return { action: 'reissue', queued, waiters }
  }
}

/* controller.save 的可外移半边（codex R3 P1 拆分）：CAS 基线现取、PUT 发起
   与响应分派。controller 只提供状态钩子（基线读写、状态机转换、计时器
   归属、排队补发入口），requestId 过期/冲突/重试语义与拆分前一致。 */
export function runTrackedSave(context: {
  put: (
    yaml: string,
    keepalive: boolean,
    expectedAt: string
  ) => Promise<{ updated_at?: string | null }>
  yaml: string
  requestId: number
  retriesLeft: number
  keepalive: boolean
  currentRequest: (id: number) => boolean
  onCleared: (requestId: number) => void
  baseline: () => string | null
  onBaseline: (
    saved: string,
    updatedAt: string | null,
    current: boolean
  ) => void
  onSaving: () => void
  onConflict: (serverYaml: string | null, serverAt: string | null) => void
  onTransientError: () => void
  armRetry: (timer: ReturnType<typeof setTimeout>) => void
  save: (
    yaml: string,
    requestId: number,
    retriesLeft: number,
    keepalive: boolean
  ) => Promise<DraftSaveFlushResult>
  finish: (ok: boolean) => DraftSaveFlushResult
}): Promise<DraftSaveFlushResult> {
  const { yaml, requestId, retriesLeft, keepalive } = context
  context.onSaving()
  const expectedAt = context.baseline() ?? DRAFT_NEVER_SAVED
  return new Promise<DraftSaveFlushResult>((resolve) =>
    runSave({
      put: context.put,
      yaml,
      keepalive,
      expectedAt,
      requestId,
      isCurrentRequest: context.currentRequest,
      clearInFlight: () => context.onCleared(requestId),
      onSuccess: context.onBaseline,
      onFailure: (error: unknown, resolveRetry: (ok: boolean) => void) => {
        if (error instanceof WorkflowDraftConflictError) {
          const current = error.currentDraft
          context.onConflict(current.definition_yaml, current.updated_at)
          return resolveRetry(false)
        }
        context.onTransientError()
        if (retriesLeft === 0) return resolveRetry(false)
        const attempt = MAX_PUT_RETRIES - retriesLeft + 1
        context.armRetry(
          setTimeout(() => {
            if (!context.currentRequest(requestId)) return resolveRetry(true)
            context
              .save(yaml, requestId, retriesLeft - 1, false)
              .then((r) => resolveRetry(r.ok))
          }, RETRY_BASE_MS * attempt)
        )
      },
      resolve: (ok) => resolve(context.finish(ok)),
    })
  )
}

/* drain 的执行半边（决策 + 派发，codex R3 P1）：一次只放行一个补发。 */
export function drainQueue(
  queue: DraftSaveQueue,
  context: {
    inFlight: number
    currentRequest: (id: number) => boolean
    inConflict: boolean
    staleResult: (ok: boolean) => DraftSaveFlushResult
    onConflictHold: (queued: QueuedSave) => void
    reissue: (queued: QueuedSave) => Promise<DraftSaveFlushResult>
  }
): void {
  const snapshot = queue.take()
  const decision = DraftSaveQueue.decide(
    snapshot,
    context.inFlight,
    context.currentRequest,
    context.inConflict
  )
  if (decision.action === 'wait')
    queue.restore(snapshot.queued!, snapshot.waiters)
  if (decision.action === 'none' || decision.action === 'abort')
    decision.staleWaiters.forEach((r) => r(context.staleResult(true)))
  if (decision.action === 'conflict') {
    context.onConflictHold(decision.queued)
    decision.staleWaiters.forEach((r) => r(context.staleResult(false)))
  }
  if (decision.action === 'reissue') {
    const promise = context.reissue(decision.queued)
    decision.waiters.forEach((resolve) => promise.then(resolve))
  }
}
