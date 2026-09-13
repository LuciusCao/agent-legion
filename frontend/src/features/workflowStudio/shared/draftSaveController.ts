import {
  DRAFT_NEVER_SAVED,
  WorkflowDraftConflictError,
} from '../../../api/workflowDraft'
import {
  DEBOUNCE_MS,
  MAX_PUT_RETRIES,
  RETRY_BASE_MS,
  IDLE_DRAFT_SAVE,
  withinKeepaliveLimit,
  type DraftSaveFlushResult,
  type DraftSaveState,
  type PutWorkflowDraftFn,
} from './draftSaveTypes'
import {
  conflictClearedState,
  conflictEnteredState,
  conflictResolvedState,
  decideSchedule,
  pendingAfterResolve,
  revertedState,
  runSave,
  stopTimer,
} from './draftSaveConflict'

/* 保存状态机（非 React）：800ms debounce 自动保存、flushNow 立即落盘、
   PUT 失败指数退避重试（≤2 次，仍败保持 error，后续编辑会重新调度）。
   并发规则：每次调度递增 requestId，迟到的响应/重试发现 requestId 过期即
   作废（last-write-wins）；回退到已持久化值且仍有在途写入时照常补存。
   作废的成功响应仍推进 CAS 基线（#633 codex review R2 P1）——它是服务端
   真值，后续请求必须以它竞争，否则连续编辑会被误报成会话冲突。
   #633：PUT 携带 CAS 基线，409 冲突进入专属 conflict 态（不自动重试
   ——同一过期时间戳重试只会再 409），同时把 CAS 基线推进到冲突响应的
   current_draft.updated_at（服务端真值），用户继续编辑后的下一次保存以新
   基线竞争；conflict 在用户采用服务端草稿（hydrate）或继续编辑（新调度）
   后解除。状态/常量在 draftSaveTypes.ts，提示文本在 draftSaveText.ts
   （#633 拆出）。 */
export class DraftSaveController {
  private state: DraftSaveState = IDLE_DRAFT_SAVE
  private readonly listeners = new Set<(state: DraftSaveState) => void>()
  private lastPersisted: string | null = null
  // #633：已持久化基线的 CAS 时间戳（null = 尚无基线，首存用 never-saved）。
  private lastPersistedAt: string | null = null
  private requestCounter = 0
  // 最新一次已发起 PUT 的 requestId（0 = 无在途写入）。
  private inFlight = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private pendingSave: { yaml: string; requestId: number } | null = null

  constructor(private readonly put: PutWorkflowDraftFn) {}

  subscribe(listener: (state: DraftSaveState) => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /* 记录服务端已持久化基线（GET 草稿到达时调用一次；#633 codex review
     P1-2：服务端草稿前进且画布采用了它时再次调用）。#633：同时记下
     updated_at 作为后续 PUT 的 CAS 基线——冲突恢复采用服务端草稿与首次
     hydrate 走同一入口。kimi review P1-2：hydrate 即冲突的解除路径之一
     （采用服务端版本），conflict 字段一并清零。 */
  hydrate(persistedYaml: string, updatedAt: string | null | undefined) {
    this.lastPersisted = persistedYaml
    this.lastPersistedAt = updatedAt ?? null
    this.setState(conflictResolvedState(this.state, updatedAt))
  }

  /* #633 codex review P1-2/P2-1：进入 conflict 态的统一入口——409 冲突
     响应（current_draft）与 turn-end 失效后服务端草稿前进（useDraftServerSync
     在用户有本地编辑时调用）共用。冲突双方携带服务端真值：把 CAS 基线推进
     到服务端 updated_at（下一次保存以新基线竞争，否则同一过期时间戳永远
     409）；用户未落盘的编辑保留在画布（conflictDraftYaml 供 UI 提供采用
     服务端草稿的入口），不自动重试。 */
  enterConflict(serverYaml: string | null, serverAt: string | null) {
    if (serverAt) this.lastPersistedAt = serverAt
    this.setState(conflictEnteredState(this.state, serverYaml, serverAt))
  }

  /* draftYaml 变化时调度一次 debounce 保存；空内容与「回退到已持久化值且
     无在途写入」不发起 PUT。kimi review P1-2/P2-4：conflict 态挂起
     autosave——用户对 Agent 改了什么零知情时一个按键即不可逆覆盖 Agent
     版本；编辑照常进 pendingSave（显示未保存），但 PUT 前必须显式解除
     冲突（继续保存 = keep-mine，adoptServerDraft = 采用服务端）。 */
  schedule(yaml: string) {
    const decision = decideSchedule(
      yaml,
      yaml === this.lastPersisted,
      this.inFlight !== 0,
      this.state.conflict === true
    )
    if (decision.action === 'skip') return
    if (decision.action === 'revert') return this.revertToPersisted()
    const requestId = (this.requestCounter += 1)
    this.pendingSave = { yaml, requestId }
    this.clearTimers()
    this.setState({ ...this.state, status: 'pending' })
    if (decision.armTimer) this.armSave(yaml, requestId)
  }

  /* debounce 到期后的 PUT 发起（schedule 正常路径与冲突解除的补发共用）。 */
  private armSave(yaml: string, requestId: number) {
    this.timer = setTimeout(() => {
      this.timer = this.pendingSave = null
      this.save(yaml, requestId, MAX_PUT_RETRIES, false)
    }, DEBOUNCE_MS)
  }

  /* kimi review P1-2：冲突显式解除——keep-mine（用户看过警示后继续保存
     本页编辑，以已推进的基线竞争）或 adopt（adoptServerDraft）。冲突未
     解除时 pendingSave 不发起 PUT（挂起 autosave），pagehide 也不自动
     flush（flushNow 对 conflict 态 no-op，防止关闭页面前 keepalive PUT
     静默覆盖 Agent 的草稿）。 */
  resolveConflict(keepMine: boolean): void {
    if (!this.state.conflict) return
    const pending = pendingAfterResolve(this.pendingSave, keepMine)
    this.setState(conflictClearedState(this.state))
    if (pending) this.armSave(pending.yaml, pending.requestId)
  }

  /* kimi review P1-2：采用服务端草稿（Agent 的版本）——经 hydrate 入口
     写入画布并推进基线，本页未保存编辑被放弃（调用方负责 UI 确认）。 */
  adoptServerDraft(serverYaml: string, serverAt: string | null): void {
    this.abortPending()
    this.hydrate(serverYaml, serverAt)
  }

  /* 回退到已持久化值：撤销等待中的保存与失败重试（retryTimer 本身也有
     requestId 护栏，这里显式清理），并把可见状态从 pending/error 收回。
     kimi review P2-3：画布回到已持久化内容即冲突已消解（本页与服务端
     一致），conflict 标记一并清除，避免红字常驻 + 保存按钮卡死。 */
  private revertToPersisted() {
    this.abortPending()
    this.setState(revertedState(this.state))
  }

  /* 立即落盘：取消 pending 的 debounce 直接 PUT；无 pending 时是 no-op
     （在途写入会自然完成；error 重试由上层先重新 schedule）。keepalive 用于
     pagehide 场景，此时不重试。
     #429：返回本次 PUT 的 promise（含重试链）并携带终态（ok + live
     state）——失败不 reject，等待方读 result.ok，不读 React 快照（闭包
     捕获的是调用前的值）；no-op / 被后续编辑作废（requestId 过期）时
     ok:true，等待方继续自己的重读校准。
     kimi review P1-2：conflict 态 no-op——pagehide/beforeunload 的自动
     flush 不得用 keepalive PUT 静默覆盖 Agent 的草稿（那正是用户想保留
     的版本）；显式路径（resolveConflict(keep-mine)）之外不发 PUT。 */
  flushNow(options?: { keepalive?: boolean }): Promise<DraftSaveFlushResult> {
    const pending = this.pendingSave
    if (!pending || this.state.conflict)
      return Promise.resolve(this.result(true))
    this.clearTimers()
    this.pendingSave = null
    const keepalive = !!options?.keepalive && withinKeepaliveLimit(pending.yaml)
    return this.save(
      pending.yaml,
      pending.requestId,
      keepalive ? 0 : MAX_PUT_RETRIES,
      keepalive
    )
  }

  /* beforeunload 护栏读法：pending（未落盘）/在途写入/失败未恢复/冲突挂起
     （本页编辑未保存且自动 flush 已停）都算未保存——离开前提示用户。 */
  hasUnsaved(): boolean {
    return (
      this.pendingSave !== null ||
      this.inFlight !== 0 ||
      this.state.status === 'error' ||
      this.state.conflict === true
    )
  }

  /* 卸载/workspace 切换：清理计时器（pending 的尾部编辑随 debounce 窗口
     丢弃，与旧行为一致；页面级离开由 useDraftUnloadGuard 的 flush 覆盖）。 */
  dispose() {
    this.abortPending()
  }

  /* 发起一次 PUT 并跟踪其终态。#429：返回 promise 供 flushNow 的调用方
     await——失败同样 resolve，迟到的响应/重试发现 requestId 过期时 resolve
     （不构成失败信号）。
     #633：PUT 携带发起时刻的 CAS 基线（同一 requestId 的重试链固定用首次
     快照）；409 冲突直接进 conflict 态且不重试，flush 终态 ok=false（等待方
     如发布确认必须中止——本页草稿并未落盘）。冲突响应同时把基线推进到
     current_draft.updated_at（服务端真值）：用户的编辑保留在画布，下一次
     保存以新基线重新竞争而不是永远 409。 */
  private save(
    yaml: string,
    requestId: number,
    retriesLeft: number,
    keepalive: boolean
  ): Promise<DraftSaveFlushResult> {
    this.inFlight = requestId
    this.setState({ ...this.state, status: 'saving' })
    const expectedAt = this.lastPersistedAt ?? DRAFT_NEVER_SAVED
    return new Promise<DraftSaveFlushResult>((resolve) =>
      runSave({
        put: this.put,
        yaml,
        keepalive,
        expectedAt,
        requestId,
        isCurrentRequest: (id) => this.requestCounter === id,
        clearInFlight: (id) => {
          if (this.inFlight === id) this.inFlight = 0
        },
        onSuccess: (saved, updatedAt, current) => {
          this.lastPersisted = saved
          this.lastPersistedAt = updatedAt
          if (current) this.setState({ status: 'saved', savedAt: updatedAt })
        },
        onFailure: (error, resolveRetry) => {
          if (error instanceof WorkflowDraftConflictError) {
            const current = error.currentDraft
            this.enterConflict(current.definition_yaml, current.updated_at)
            return resolveRetry(false)
          }
          this.setState({ ...this.state, status: 'error' })
          if (retriesLeft === 0) return resolveRetry(false)
          const attempt = MAX_PUT_RETRIES - retriesLeft + 1
          this.retryTimer = setTimeout(() => {
            this.retryTimer = null
            if (this.requestCounter !== requestId) return resolveRetry(true)
            this.save(yaml, requestId, retriesLeft - 1, false).then((r) =>
              resolveRetry(r.ok)
            )
          }, RETRY_BASE_MS * attempt)
        },
        resolve: (ok) => resolve(this.result(ok)),
      })
    )
  }

  /* flush 终态工厂：ok=false 让发布确认中止（不发布未落盘的旧草稿）。 */
  private result(ok: boolean): DraftSaveFlushResult {
    return { ok, state: this.state }
  }

  private setState(next: DraftSaveState) {
    this.state = next
    this.listeners.forEach((listener) => listener(next))
  }

  /* 作废 pending 保存：双清计时器 + 递增 requestId（在途响应作废）。 */
  private abortPending() {
    this.clearTimers()
    this.requestCounter += 1
    this.pendingSave = null
  }

  private clearTimers() {
    this.timer = stopTimer(this.timer)
    this.retryTimer = stopTimer(this.retryTimer)
  }
}
