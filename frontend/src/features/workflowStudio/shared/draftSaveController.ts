import { MAX_PUT_RETRIES, type DraftSaveFlushResult } from './draftSaveTypes'
import { drainQueue, runTrackedSave } from './draftSaveQueue'
import { DraftSaveLifecycle } from './draftSaveLifecycle'

/* 保存状态机（非 React）：800ms debounce 自动保存、flushNow 立即落盘、
   PUT 失败指数退避重试（≤2 次，仍败保持 error，后续编辑会重新调度）。
   并发规则：每次调度递增 requestId，迟到的响应/重试发现 requestId 过期即
   作废（last-write-wins）；回退到已持久化值且仍有在途写入时照常补存。
   作废的成功响应仍推进 CAS 基线（#633 codex review R2 P1）——它是服务端
   真值，后续请求必须以它竞争，否则连续编辑会被误报成会话冲突。
   #633：PUT 携带 CAS 基线，409 冲突进入专属 conflict 态（不自动重试
   ——同一落后基线重试只会再 409），同时把 CAS 基线推进到冲突响应的
   current_draft 代次（服务端真值），用户继续编辑后的下一次保存以新
   基线竞争；conflict 在用户采用服务端草稿（hydrate）或继续编辑（新调度）
   后解除。#1226 五轮收口：基线货币是草稿代次（generation），不再是
   updated_at 时间戳。状态/常量在 draftSaveTypes.ts，提示文本在 draftSaveText.ts
   （#633 拆出），调度/冲突生命周期在 draftSaveLifecycle.ts（#1000 拆出）。
   #1177 评审缺陷族：核心不变量「内容偏离已持久化 ⇒ status 必离开 settled
   （saved/idle）」由全部路径共同维护——调度（含空白 skip 的
   markBlankSkipped）、冲突解除（resolveConflict 保留挂起编辑）、强制
   写回（forceSave 空白护栏）。消费方（stale hint 短路/自动校验/保存徽章/
   beforeunload 守卫）都建立在该不变量之上，新增路径不得绕过。 */
export class DraftSaveController extends DraftSaveLifecycle {
  /* 发起一次 PUT 并跟踪其终态。#429：返回 promise 供 flushNow 的调用方
     await——失败同样 resolve，迟到的响应/重试发现 requestId 过期时 resolve
     （不构成失败信号）。
     #633：PUT 携带发起时刻的 CAS 基线（同一 requestId 的重试链固定用首次
     快照）；409 冲突直接进 conflict 态且不重试，flush 终态 ok=false（等待方
     如发布确认必须中止——本页草稿并未落盘）。冲突响应同时把基线推进到
     current_draft 的代次（服务端真值）：用户的编辑保留在画布，下一次
     保存以新基线重新竞争而不是永远 409。 */
  protected save(
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
      baseline: () => this.lastPersistedGeneration,
      onBaseline: (saved, at, current, hash, generation) => {
        this.lastPersisted = saved
        if (generation != null) this.lastPersistedGeneration = generation
        // #1177 评审 V6：作废响应同样推进 savedHash——它是服务端真值的
        // 身份；pending 期间没有消费方按 settled 读它，而画布 revert 到
        // 该内容后它恰好正确（否则一张记录旧 hash 的卡可短路隐藏提示）。
        if (current)
          this.setState({
            status: 'saved',
            savedAt: at,
            savedHash: hash,
            saveErrorMessage: null,
          })
        else this.setState({ ...this.state, savedHash: hash })
      },
      onSaving: () =>
        this.setState({
          ...this.state,
          status: 'saving',
          saveErrorMessage: null,
        }),
      onConflict: this.enterConflict.bind(this),
      onTransientError: (terminal, message) =>
        this.setState({
          ...this.state,
          status: 'error',
          saveError: terminal ? 'terminal' : 'retrying',
          saveErrorMessage: message,
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
}
