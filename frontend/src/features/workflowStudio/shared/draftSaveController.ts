import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'

export type DraftSaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error'
export type DraftSaveState = {
  status: DraftSaveStatus
  savedAt: string | null
  /** GET 草稿查询失败（仅内存模式）时由组合层合并进来，供 UI 警示。 */
  loadError?: boolean
}

export const IDLE_DRAFT_SAVE: DraftSaveState = { status: 'idle', savedAt: null }

const DEBOUNCE_MS = 800
const MAX_PUT_RETRIES = 2
const RETRY_BASE_MS = 2000
// fetch keepalive 请求体上限约 64KiB（按 UTF-8 字节计），留安全余量；超出
// 退化为普通 PUT（pagehide 下尽力而为，不再享受 keepalive 的存活保证）。
const KEEPALIVE_MAX_BODY_BYTES = 60_000

// yaml.length 是 UTF-16 码元数，中文草稿会严重低估体积；必须对完整 JSON
// 请求体按 UTF-8 字节数判断。
function withinKeepaliveLimit(yaml: string): boolean {
  const body = JSON.stringify({ definition_yaml: yaml })
  return new TextEncoder().encode(body).byteLength <= KEEPALIVE_MAX_BODY_BYTES
}

export type PutWorkflowDraftFn = (
  yaml: string,
  keepalive: boolean
) => Promise<WorkflowDraftStoreResponse>

/** #429 收尾 P2-1：flushNow 的结果——本次 flush 是否把最新内容落到了服务端。
 * controller 全路径 resolve 不 reject（失败态进 state），调用方（agent 发布
 * 确认）必须在 await 之后读这个终态，不能读 React useState 快照的
 * studio.draftSave——闭包捕获的是点击那一刻的 state，await 期间 PUT 失败
 * 落定的 error 态快照链路看不见，守卫会漏过并发布旧草稿。 */
export type DraftSaveFlushResult = {
  /** true：本次 flush 的 PUT 成功（最新内容已落盘）。 */
  ok: boolean
  /** 落定后的 controller 终态（live 引用，非 React 快照）。 */
  state: DraftSaveState
}

/** 保存状态的一句话提示（顶栏状态文本）：saving/error/pending 与 GET 失败
 * 警示优先，否则带最近保存时间（HH:MM，本地时区）。 */
export function draftSaveText(save: DraftSaveState | undefined): string | null {
  if (!save) return null
  if (save.loadError) return '草稿服务不可用，编辑仅保留在本页内存'
  if (save.status === 'saving') return '草稿保存中…'
  if (save.status === 'error') return '草稿保存失败，将自动重试'
  if (save.status === 'pending') return '草稿有未保存更改'
  if (!save.savedAt) return null
  const at = new Date(save.savedAt)
  const hh = String(at.getHours()).padStart(2, '0')
  const mm = String(at.getMinutes()).padStart(2, '0')
  return `草稿已保存 ${hh}:${mm}`
}

/** 草稿保存状态机（非 React）：800ms debounce 自动保存、flushNow 立即落盘、
 * PUT 失败指数退避重试（≤2 次，仍败保持 error，后续编辑会重新调度尝试）。
 * 并发规则：每次调度递增 requestId，迟到的响应/重试发现 requestId 过期即
 * 作废（last-write-wins）；回退到已持久化值且仍有在途写入时照常补存，把
 * 服务端可能已收到的撤销值改回来。UI 可见五态：idle（干净/未保存过）→
 * pending（有未落盘编辑，debounce 等待中）→ saving（PUT 在途）→ saved
 * （已落盘）/ error（重试耗尽仍失败，保留 savedAt）。 */
export class DraftSaveController {
  private state: DraftSaveState = IDLE_DRAFT_SAVE
  private readonly listeners = new Set<(state: DraftSaveState) => void>()
  private lastPersisted: string | null = null
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

  /** 记录服务端已持久化基线（GET 草稿到达时调用一次）。 */
  hydrate(persistedYaml: string, updatedAt: string | null | undefined) {
    this.lastPersisted = persistedYaml
    if (updatedAt) {
      this.setState({ status: 'idle', savedAt: updatedAt })
    }
  }

  /** draftYaml 变化时调度一次 debounce 保存；空内容与「回退到已持久化值且
   * 无在途写入」不发起 PUT。 */
  schedule(yaml: string) {
    if (!yaml.trim()) return
    if (yaml === this.lastPersisted && this.inFlight === 0) {
      // 回退到已持久化值：撤销等待中的保存与失败重试（retryTimer 本身也有
      // requestId 护栏，这里显式清理），并把可见状态从 pending/error 收回。
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
      return
    }
    const requestId = (this.requestCounter += 1)
    this.pendingSave = { yaml, requestId }
    this.clearTimer()
    this.clearRetryTimer()
    this.setState({ ...this.state, status: 'pending' })
    this.timer = setTimeout(() => {
      this.timer = null
      this.pendingSave = null
      this.save(yaml, requestId, MAX_PUT_RETRIES, false)
    }, DEBOUNCE_MS)
  }

  /** 立即落盘：取消 pending 的 debounce 直接 PUT；无 pending 时是 no-op
   * （在途写入会自然完成；error 重试由上层先重新 schedule）。keepalive 用于
   * pagehide 场景，此时不重试（页面即将销毁）。
   * #429 四轮 P2-1：返回本次 PUT 的 promise（含内部重试链）——调用方可以
   * await 到「落盘完成」再发起依赖服务端草稿的操作（agent 发布确认）。
   * #429 收尾 P2-1：promise 携带本次 flush 的终态（ok + controller 的 live
   * state）——失败不 reject（错误态已进 state，UI 有可见警示），等待方据
   * result.ok 决定是否继续，而不是读 React 快照里的 status（闭包捕获的是
   * 调用前的值，await 期间落定的 error 态快照链路看不见——守卫会漏过）。
   * no-op（无 pending）返回 ok:true（无内容需要落盘 = 无失败）；PUT 被后续
   * 编辑作废（requestId 过期）时 ok:true——该次保存已不代表最新内容，等待
   * 方应继续走自己的重读校准。keepalive 模式（页面即将销毁）无有意义的完成
   * 时序，同样返回结果但调用方不该依赖它。 */
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

  /** 发起一次 PUT 并跟踪其终态。#429 四轮 P2-1：返回 promise 供 flushNow
   * 的调用方 await——失败同样 resolve（错误态已进 state，UI 有可见警示；
   * 等待方据此走重读校准/放弃后续操作，而不是把保存错误混进自己的错误
   * 流）。迟到的响应/重试发现 requestId 过期时 resolve（该次保存已被更新
   * 的编辑取代，不构成对等待方的失败信号）。#429 收尾 P2-1：resolve 值是
   * 本次 flush 的终态结果（ok + live state），见 DraftSaveFlushResult。 */
  private save(
    yaml: string,
    requestId: number,
    retriesLeft: number,
    keepalive: boolean
  ): Promise<DraftSaveFlushResult> {
    this.inFlight = requestId
    this.setState({ ...this.state, status: 'saving' })
    return new Promise<DraftSaveFlushResult>((resolve) => {
      this.put(yaml, keepalive)
        .then((response) => {
          if (this.inFlight === requestId) this.inFlight = 0
          if (this.requestCounter !== requestId) return resolve(this.successResult())
          this.lastPersisted = yaml
          this.setState({
            status: 'saved',
            savedAt: response.updated_at ?? null,
          })
          resolve(this.successResult())
        })
        .catch(() => {
          if (this.inFlight === requestId) this.inFlight = 0
          if (this.requestCounter !== requestId) return resolve(this.successResult())
          this.setState({ ...this.state, status: 'error' })
          if (retriesLeft > 0) {
            const attempt = MAX_PUT_RETRIES - retriesLeft + 1
            this.retryTimer = setTimeout(() => {
              this.retryTimer = null
              if (this.requestCounter !== requestId) return resolve(this.successResult())
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
