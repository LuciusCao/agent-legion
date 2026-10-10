import {
  MAX_PUT_RETRIES,
  DEBOUNCE_MS,
  IDLE_DRAFT_SAVE,
  withinKeepaliveLimit,
  type DraftSaveFlushResult,
  type DraftSaveState,
  type PutWorkflowDraftFn,
} from './draftSaveTypes'
import { drainQueue, DraftSaveQueue, runTrackedSave } from './draftSaveQueue'
import {
  conflictClearedState,
  conflictEnteredState,
  conflictResolvedState,
  decideSchedule,
  hasPendingWork,
  pendingAfterResolve,
  revertedState,
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
   （#633 拆出）。
   #1177 评审缺陷族：核心不变量「内容偏离已持久化 ⇒ status 必离开 settled
   （saved/idle）」由全部路径共同维护——调度（含空白 skip 的
   markBlankSkipped）、冲突解除（resolveConflict 保留挂起编辑）、强制
   写回（forceSave 空白护栏）。消费方（stale hint 短路/自动校验/保存徽章/
   beforeunload 守卫）都建立在该不变量之上，新增路径不得绕过。 */
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
  // codex R3 P1：在途 PUT 期间的下一次保存排队（见 draftSaveQueue.ts）。
  private readonly queue = new DraftSaveQueue()

  constructor(private readonly put: PutWorkflowDraftFn) {}

  subscribe(listener: (state: DraftSaveState) => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /* 记录服务端已持久化基线（GET 草稿到达时调用一次；#633 codex review
     P1-2：服务端草稿前进且画布采用了它时再次调用）。#633：同时记下
     updated_at 作为后续 PUT 的 CAS 基线——冲突恢复采用服务端草稿与首次
     hydrate 走同一入口。kimi review P1-2：hydrate 即冲突的解除路径之一
     （采用服务端版本），conflict 字段一并清零。#1143：savedHash 是服务端
     草稿的语义身份（GET/adopt 传入），随基线一并推进。 */
  hydrate = (
    persistedYaml: string,
    updatedAt: string | null | undefined,
    savedHash?: string | null
  ): void => {
    this.lastPersisted = persistedYaml
    this.lastPersistedAt = updatedAt ?? null
    this.setState(conflictResolvedState(this.state, updatedAt, savedHash))
  }

  /* #633 codex review P1-2/P2-1：进入 conflict 态的统一入口——409 冲突
     响应（current_draft）与 turn-end 失效后服务端草稿前进（useDraftServerSync
     在用户有本地编辑时调用）共用。冲突双方携带服务端真值：把 CAS 基线推进
     到服务端 updated_at（下一次保存以新基线竞争，否则同一过期时间戳永远
     409）；用户未落盘的编辑保留在画布（conflictDraftYaml 供 UI 提供采用
     服务端草稿的入口），不自动重试。R4 P1：同时取消挂起的 debounce/retry
     计时器——计时器若存活，到期 save() 会以刚推进的基线成功覆盖 Agent
     版本，绕过「用户显式二选一」的保护。 */
  enterConflict = (
    yaml: string | null,
    at: string | null,
    hash?: string | null
  ): void => {
    this.clearTimers()
    this.pendingSave = null
    if (at) this.lastPersistedAt = at
    this.setState(conflictEnteredState(this.state, yaml, at, hash))
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
    if (decision.action === 'skip') return this.markBlankSkipped()
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
     静默覆盖 Agent 的草稿）。
     #804 P1-A：409 于 PUT 在途时到达的冲突，pendingSave 已被清空——
     keep-mine 拿不到 pending 时按调用方给的当前画布内容无条件补调度，
     否则状态机卡 error：draftYaml 未变不再触发调度 effect、flushNow
     no-op，编辑永不落盘、自动校验/发布门控随之锁死。 */
  resolveConflict(keepMine: boolean, currentYaml?: () => string): void {
    if (!this.state.conflict) return
    if (!keepMine && this.pendingSave) {
      // #1177 评审 V3：UI 只在无可采用草稿（conflictDraftYaml == null 的
      // never-saved 竞态）时走这条「仅解除警示」路径——丢弃 pendingSave
      // 会让画布上的未落盘编辑被状态机遗忘（status 收敛 saved、
      // hasUnsaved=false，关页静默丢失）。按冲突挂起的既有形态保留
      // pending（不 arm 计时器）：hasUnsaved 守卫有原料，下一次按键经
      // schedule 重新 arm，flush-first 也可补发。
      const suspended = this.pendingSave
      this.setState({ ...conflictClearedState(this.state), status: 'pending' })
      this.pendingSave = suspended
      return
    }
    const pending = pendingAfterResolve(this.pendingSave, keepMine)
    this.setState(conflictClearedState(this.state))
    if (pending) {
      // #1177 评审 P3-1：补发前先把 status 推到 pending（与 schedule
      // 对称）——否则 conflictCleared 收敛的 saved/idle 会覆盖整个
      // debounce 窗口，窗口内「settled 但内容未落盘」的假状态重现。
      this.setState({ ...this.state, status: 'pending' })
      this.armSave(pending.yaml, pending.requestId)
      return
    }
    if (keepMine && currentYaml) this.forceSave(currentYaml())
  }

  /* keep-mine 补救的强制写回（#804 轮 8 P1）：普通 schedule 的去重会把
     「内容 == 已持久化值」判成 revert 不发 PUT——但冲突语义是以新 CAS
     基线把画布内容写回服务端（Agent 已把服务端推进成别的内容），必须
     绕过去重强制发，否则警示消失而内容从未写回，离页即丢。
     #1177 评审 V5：空白无可落盘——conflictCleared 已把 status 收敛到
     saved/idle，不把假 settled 留给空白画布（保持 pending）。 */
  private forceSave(yaml: string) {
    if (!yaml.trim()) {
      this.setState({ ...this.state, status: 'pending' })
      return
    }
    const requestId = (this.requestCounter += 1)
    this.pendingSave = { yaml, requestId }
    this.setState({ ...this.state, status: 'pending' })
    this.armSave(yaml, requestId)
  }

  /* kimi review P1-2：采用服务端草稿（Agent 的版本）——经 hydrate 入口
     写入画布并推进基线，本页未保存编辑被放弃（调用方负责 UI 确认）。
     #1143：hash 即被采用草稿的语义身份，随 hydrate 恢复成 savedHash
     （画布内容=服务端草稿，草稿卡核对不应对它误报）。 */
  adoptServerDraft(yaml: string, at: string | null, hash?: string | null) {
    this.abortPending()
    this.hydrate(yaml, at, hash)
  }

  /* 回退到已持久化值：撤销等待中的保存与失败重试（retryTimer 本身也有
     requestId 护栏，这里显式清理），并把可见状态从 pending/error 收回。
     kimi review P2-3：画布回到已持久化内容即冲突已消解（本页与服务端
     一致），conflict 标记一并清除，避免红字常驻 + 保存按钮卡死。 */
  private revertToPersisted = (): void => {
    this.abortPending()
    this.setState(revertedState(this.state))
  }

  /* 空白 skip 的状态收口（#1177 评审缺陷族 V1/V2）：空白永不落盘（服务端
     拒存），但「跳过 PUT」不等于「无事发生」——
     1. V2：撤销等待中的保存/重试/排队——debounce 窗口内的旧内容不得在
        用户清空画布后照旧到期落盘（「清空即放弃」的意图被静默违背）；
     2. V1：上次成功保存的 settled 状态不得原样保留——settled ⇒ 当前
        内容已落盘，而空白与任何已落盘内容都不同（stale hint 拿旧
        savedHash 短路隐藏分歧提示正是此洞）。
     冲突态不动 status（error 占位本就非 settled，横幅语义靠 conflict
     标记）；从未保存过（无 savedAt）保持 idle——空画布没有可偏离的
     基线。 */
  private markBlankSkipped(): void {
    this.abortPending()
    if (this.state.conflict) return
    if (this.state.savedAt && this.state.status !== 'pending') {
      this.setState({ ...this.state, status: 'pending' })
    }
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
    // #804 轮 6 H1：conflict 态的 no-op 必须报 ok:false——「没发 PUT」不是
    // 「已落盘」，等待方（agent 发布确认守卫）据此中止，与 draftSaveQueue
    // 的 conflict drain 同语义（两边不一致曾是审 A 发 B 洞）。pagehide 的
    // 自动 flush 不消费返回值，无影响。
    if (this.state.conflict) return Promise.resolve(this.result(false))
    if (!pending) return Promise.resolve(this.result(true))
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
    return hasPendingWork(
      this.pendingSave !== null,
      this.inFlight !== 0,
      this.state.status === 'error',
      this.state.conflict === true
    )
  }

  /* 卸载/workspace 切换：清理计时器（pending 的尾部编辑随 debounce 窗口
     丢弃，与旧行为一致；页面级离开由 useDraftUnloadGuard 的 flush 覆盖）。 */
  dispose = (): void => this.abortPending()

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
    // codex R3 P1：串行化 PUT——在途写入未结束时本次保存排队（A 完成后
    // 以 A 推进的新基线补发），而不是用同一旧基线并发竞争。flushNow 的
    // 调用方等待的是整条链的终态：排队请求 resolve 随补发 PUT 的结果。
    if (this.inFlight !== 0 && this.inFlight !== requestId) {
      return new Promise<DraftSaveFlushResult>((resolve) =>
        this.queue.enqueue({ yaml, requestId }, resolve)
      )
    }
    this.inFlight = requestId
    return runTrackedSave({
      put: this.put,
      yaml,
      requestId,
      retriesLeft,
      keepalive,
      currentRequest: this.isCurrent,
      onCleared: (id) => {
        if (this.inFlight === id) this.inFlight = 0
        this.drainQueued()
      },
      baseline: () => this.lastPersistedAt,
      onBaseline: (saved, at, current, hash) => {
        this.lastPersisted = saved
        this.lastPersistedAt = at
        // #1177 评审 V6：作废响应同样推进 savedHash——它是服务端真值的
        // 身份；pending 期间没有消费方按 settled 读它，而画布 revert 到
        // 该内容后它恰好正确（否则一张记录旧 hash 的卡可短路隐藏提示）。
        if (current)
          this.setState({ status: 'saved', savedAt: at, savedHash: hash })
        else this.setState({ ...this.state, savedHash: hash })
      },
      onSaving: () => this.setState({ ...this.state, status: 'saving' }),
      onConflict: this.enterConflict.bind(this),
      onTransientError: (terminal) =>
        this.setState({
          ...this.state,
          status: 'error',
          saveError: terminal ? 'terminal' : 'retrying',
        }),
      armRetry: (timer) => {
        this.retryTimer = timer
      },
      save: this.save.bind(this),
      finish: (ok: boolean) => this.result(ok),
    })
  }

  /* 在途 PUT 终态后的排队补发（决策逻辑在 DraftSaveQueue.decide）：
     补发以发起时刻的基线竞争（save 内现取）。 */
  private drainQueued(): void {
    drainQueue(this.queue, {
      inFlight: this.inFlight,
      currentRequest: this.isCurrent,
      inConflict: this.state.conflict === true,
      staleResult: this.result.bind(this),
      onConflictHold: (queued) => {
        this.pendingSave = queued
      },
      reissue: (queued) =>
        this.save(queued.yaml, queued.requestId, MAX_PUT_RETRIES, false),
    })
  }

  /* flush 终态工厂：ok=false 让发布确认中止（不发布未落盘的旧草稿）。 */
  private result(ok: boolean): DraftSaveFlushResult {
    return { ok, state: this.state }
  }

  private setState(next: DraftSaveState) {
    this.state = next
    this.listeners.forEach((listener) => listener(next))
  }

  /* 作废 pending 保存：双清计时器 + 递增 requestId（在途响应作废）；排队
     保存一并作废（drainQueued 会按 requestId 过期丢弃）。 */
  private abortPending() {
    this.clearTimers()
    this.requestCounter += 1
    this.pendingSave = null
    this.queue.discard().forEach((resolve) => resolve(this.result(true)))
  }

  private isCurrent = (id: number): boolean => this.requestCounter === id

  private clearTimers() {
    this.timer = stopTimer(this.timer)
    this.retryTimer = stopTimer(this.retryTimer)
  }
}
