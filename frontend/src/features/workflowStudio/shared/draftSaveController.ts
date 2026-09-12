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

/** 保存状态机（非 React）：800ms debounce 自动保存、flushNow 立即落盘、
 * PUT 失败指数退避重试（≤2 次，仍败保持 error，后续编辑会重新调度）。
 * 并发规则：每次调度递增 requestId，迟到的响应/重试发现 requestId 过期即
 * 作废（last-write-wins）；回退到已持久化值且仍有在途写入时照常补存。
 * #633：PUT 携带 CAS 基线，409 冲突进入专属 conflict 态（不自动重试
 * ——同一过期时间戳重试只会再 409），用户采用服务端草稿（hydrate）或
 * 继续编辑（新调度）后解除。状态/常量在 draftSaveTypes.ts，提示文本在
 * draftSaveText.ts（#633 拆出）。 */
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
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** 记录服务端已持久化基线（GET 草稿到达时调用一次）。#633：同时记下
   * updated_at 作为后续 PUT 的 CAS 基线——冲突恢复采用服务端草稿与首次
   * hydrate 走同一入口。 */
  hydrate(persistedYaml: string, updatedAt: string | null | undefined) {
    this.lastPersisted = persistedYaml
    this.lastPersistedAt = updatedAt ?? null
    if (updatedAt) {
      this.setState({ status: 'idle', savedAt: updatedAt })
    }
  }

  /** draftYaml 变化时调度一次 debounce 保存；空内容与「回退到已持久化值且
   * 无在途写入」不发起 PUT。 */
  schedule(yaml: string) {
    if (!yaml.trim()) return
    if (yaml === this.lastPersisted && this.inFlight === 0) {
      this.revertToPersisted()
      return
    }
    const requestId = (this.requestCounter += 1)
    this.pendingSave = { yaml, requestId }
    this.clearTimer()
    this.clearRetryTimer()
    // 新编辑取代冲突警示（#633）：conflict 只描述「上一次保存输掉的竞争」，
    // 用户继续编辑即表达了意图，下一次保存会带新的 CAS 基线再竞争。
    this.setState({
      ...this.state,
      status: 'pending',
      conflict: false,
    })
    this.timer = setTimeout(() => {
      this.timer = null
      this.pendingSave = null
      this.save(yaml, requestId, MAX_PUT_RETRIES, false)
    }, DEBOUNCE_MS)
  }

  /** 回退到已持久化值：撤销等待中的保存与失败重试（retryTimer 本身也有
   * requestId 护栏，这里显式清理），并把可见状态从 pending/error 收回。 */
  private revertToPersisted() {
    this.clearTimer()
    this.clearRetryTimer()
    this.requestCounter += 1
    this.pendingSave = null
    if (this.state.status === 'pending' || this.state.status === 'error') {
      this.setState({
        ...this.state,
        status: this.state.savedAt ? 'saved' : 'idle',
      })
    }
  }

  /** 立即落盘：取消 pending 的 debounce 直接 PUT；无 pending 时是 no-op
   * （在途写入会自然完成；error 重试由上层先重新 schedule）。keepalive 用于
   * pagehide 场景，此时不重试。
   * #429：返回本次 PUT 的 promise（含重试链）并携带终态（ok + live
   * state）——失败不 reject，等待方读 result.ok，不读 React 快照（闭包
   * 捕获的是调用前的值）；no-op / 被后续编辑作废（requestId 过期）时
   * ok:true，等待方继续自己的重读校准。 */
  flushNow(options?: { keepalive?: boolean }): Promise<DraftSaveFlushResult> {
    const pending = this.pendingSave
    if (!pending) return Promise.resolve(this.successResult())
    this.clearTimer()
    this.pendingSave = null
    const keepalive =
      options?.keepalive === true && withinKeepaliveLimit(pending.yaml)
    return this.save(
      pending.yaml,
      pending.requestId,
      keepalive ? 0 : MAX_PUT_RETRIES,
      keepalive
    )
  }

  /** beforeunload 护栏读法：pending（未落盘）/在途写入/失败未恢复都算未保存。 */
  hasUnsaved(): boolean {
    return (
      this.pendingSave !== null ||
      this.inFlight !== 0 ||
      this.state.status === 'error'
    )
  }

  /** 卸载/workspace 切换：清理计时器（pending 的尾部编辑随 debounce 窗口
   * 丢弃，与旧行为一致；页面级离开由 useDraftUnloadGuard 的 flush 覆盖）。 */
  dispose() {
    this.clearTimer()
    this.clearRetryTimer()
    this.pendingSave = null
  }

  /** 发起一次 PUT 并跟踪其终态。#429：返回 promise 供 flushNow 的调用方
   * await——失败同样 resolve，迟到的响应/重试发现 requestId 过期时 resolve
   * （不构成失败信号）。
   * #633：PUT 携带发起时刻的 CAS 基线（同一 requestId 的重试链固定用首次
   * 快照）；409 冲突直接进 conflict 态且不重试，flush 终态 ok=false（等待方
   * 如发布确认必须中止——本页草稿并未落盘）。 */
  private save(
    yaml: string,
    requestId: number,
    retriesLeft: number,
    keepalive: boolean
  ): Promise<DraftSaveFlushResult> {
    this.inFlight = requestId
    this.setState({ ...this.state, status: 'saving' })
    const expectedAt = this.lastPersistedAt ?? DRAFT_NEVER_SAVED
    return new Promise<DraftSaveFlushResult>((resolve) => {
      this.put(yaml, keepalive, expectedAt)
        .then((response) => {
          if (this.inFlight === requestId) this.inFlight = 0
          if (this.requestCounter !== requestId)
            return resolve(this.successResult())
          this.lastPersisted = yaml
          this.lastPersistedAt = response.updated_at ?? null
          this.setState({
            status: 'saved',
            savedAt: response.updated_at ?? null,
          })
          resolve(this.successResult())
        })
        .catch((error) => {
          if (this.inFlight === requestId) this.inFlight = 0
          if (this.requestCounter !== requestId)
            return resolve(this.successResult())
          if (error instanceof WorkflowDraftConflictError) {
            this.setState({
              status: 'error',
              savedAt: this.state.savedAt,
              conflict: true,
              conflictDraftYaml: error.currentDraft.definition_yaml,
            })
            return resolve(this.failureResult())
          }
          this.setState({ ...this.state, status: 'error' })
          if (retriesLeft > 0) {
            const attempt = MAX_PUT_RETRIES - retriesLeft + 1
            this.retryTimer = setTimeout(() => {
              this.retryTimer = null
              if (this.requestCounter !== requestId)
                return resolve(this.successResult())
              this.save(yaml, requestId, retriesLeft - 1, false).then(resolve)
            }, RETRY_BASE_MS * attempt)
          } else resolve(this.failureResult())
        })
    })
  }

  /** no-op / 过期作废：等待方继续自己的重读校准（不构成失败信号）。 */
  private successResult(): DraftSaveFlushResult {
    return { ok: true, state: this.state }
  }

  /** 重试耗尽仍失败：等待方（发布确认）必须中止，不发布未落盘的旧草稿。 */
  private failureResult(): DraftSaveFlushResult {
    return { ok: false, state: this.state }
  }

  private setState(next: DraftSaveState) {
    this.state = next
    this.listeners.forEach((listener) => listener(next))
  }

  private clearTimer() {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  private clearRetryTimer() {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
  }
}
