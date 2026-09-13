/** #633 codex review P1-2：服务端草稿重应用（turn-end 失效后）的纯决策。
 *
 * 初始装载后 agent 可能在会话中途保存草稿（save_workflow_draft →
 * turn-end 查询失效 → useWorkflowDraftQuery 重取）；画布必须跟上服务端
 * 真值而不是等到整页刷新——但绝不能覆盖用户未保存的编辑。决策以
 * updated_at 为准（同一 updated_at 是 no-op，新的才是重应用候选），
 * 用户编辑检测复用 useServerDraftApply 的 touched 跟踪。 */

export type ServerDraftReapplyDecision =
  | { action: 'noop'; reason: 'not-ready' | 'same-or-older' | 'user-touched' }
  | { action: 'apply'; yaml: string; updatedAt: string }
  | { action: 'conflict'; yaml: string; updatedAt: string }

/** 比较 CAS 时间戳：ISO 字符串按时间值比较（Postgres `+00` 与 Python
 * `+00:00`/`Z` 渲染差异不影响大小关系——`+NN` 裸偏移补成 `+NN:00`）。
 * 解析失败按「不新」处理。appliedAt 为 null（尚未应用过任何服务端草稿）
 * 时任何草稿都算新——初始装载的首次应用与重应用走同一条路。 */
export function isServerDraftNewer(
  candidate: string | null | undefined,
  appliedAt: string | null | undefined
): boolean {
  if (!candidate) return false
  if (!appliedAt) return true
  const next = Date.parse(normalizeOffset(candidate))
  const base = Date.parse(normalizeOffset(appliedAt))
  if (Number.isNaN(next) || Number.isNaN(base)) return false
  return next > base
}

/** JS Date.parse 不认 `+00`/`+08` 这类不带分钟的偏移（Postgres ::text
 * 渲染）；补成 `+00:00` 形式，其余原样。 */
function normalizeOffset(value: string): string {
  return value.replace(/([+-]\d{2})$/, '$1:00')
}

/** 重应用决策：not-ready（查询未到达/基线未知）与 same-or-older（同一
 * updated_at 或乱序迟到）都是 no-op；agent 草稿前进且用户未碰本地草稿
 * → apply（画布直接采用服务端真值）；用户有本地编辑 → conflict（保留
 * 编辑，由保存层以 conflict 态向用户呈现，见 useWorkflowDraftPersistence）。
 * kimi review P1-1（幻影冲突）：用户自己的保存成功后，turn-end 重取回的
 * 正是本页刚存的草稿——内容与画布一致即 own-save 回显，静默推进基线
 * （apply 到相同内容 + 清 touched），不升起冲突警示。 */
export function decideServerDraftReapply(input: {
  serverDraftYaml: string | null | undefined
  serverDraftUpdatedAt: string | null | undefined
  appliedUpdatedAt: string | null | undefined
  userTouched: boolean
  canvasYaml?: string
}): ServerDraftReapplyDecision {
  const {
    serverDraftYaml,
    serverDraftUpdatedAt,
    appliedUpdatedAt,
    userTouched,
    canvasYaml,
  } = input
  if (serverDraftYaml === undefined || serverDraftYaml === null) {
    return { action: 'noop', reason: 'not-ready' }
  }
  if (!isServerDraftNewer(serverDraftUpdatedAt, appliedUpdatedAt)) {
    return { action: 'noop', reason: 'same-or-older' }
  }
  // own-save 回显：服务端草稿即画布当前内容——不是外部变更，推进基线。
  if (canvasYaml !== undefined && serverDraftYaml === canvasYaml) {
    return {
      action: 'apply',
      yaml: serverDraftYaml,
      updatedAt: serverDraftUpdatedAt ?? '',
    }
  }
  if (userTouched) {
    return {
      action: 'conflict',
      yaml: serverDraftYaml,
      updatedAt: serverDraftUpdatedAt ?? '',
    }
  }
  return {
    action: 'apply',
    yaml: serverDraftYaml,
    updatedAt: serverDraftUpdatedAt ?? '',
  }
}

/** 非React的应用跟踪器：记录已应用的 updated_at 与「用户碰过」标记，
 * 暴露 evaluate（供 effect 调用）与 markTouched（供 setter 调用）。
 * workspace 切换由外部重建实例（useServerDraftApply 的 reset effect）。 */
export class ServerDraftApplyTracker {
  private appliedAt: string | null = null
  private touched = false
  private conflict: { yaml: string; updatedAt: string } | null = null

  markTouched(): void {
    this.touched = true
    this.conflict = null
  }

  consumeConflict(): { yaml: string; updatedAt: string } | null {
    const conflict = this.conflict
    this.conflict = null
    return conflict
  }

  /** 评估一次服务端草稿：apply 时写画布并推进 appliedAt；conflict 时挂起
   * 冲突通知（由 consumeConflict 消费）。canvasYaml 参与 own-save 回显
   * 判定（kimi review P1-1）：服务端草稿与画布一致时走 apply（内容相同，
   * 实际只是推进基线），不误报冲突。 */
  evaluate(
    serverDraftYaml: string | null | undefined,
    serverDraftUpdatedAt: string | null | undefined,
    applyToCanvas: (yaml: string) => void,
    canvasYaml?: string
  ): 'apply' | 'conflict' | 'noop' {
    const decision = decideServerDraftReapply({
      serverDraftYaml,
      serverDraftUpdatedAt,
      appliedUpdatedAt: this.appliedAt,
      userTouched: this.touched,
      canvasYaml,
    })
    if (decision.action === 'apply') {
      this.appliedAt = decision.updatedAt
      this.conflict = null
      applyToCanvas(decision.yaml)
      return 'apply'
    }
    if (decision.action === 'conflict') {
      this.conflict = { yaml: decision.yaml, updatedAt: decision.updatedAt }
      return 'conflict'
    }
    return 'noop'
  }
}
