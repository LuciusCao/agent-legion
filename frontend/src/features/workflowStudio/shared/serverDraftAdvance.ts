/** #633 codex review P1-2：服务端草稿前进判定与重应用的纯决策（从
 * serverDraftReapply.ts 拆出，文件体积预算——状态跟踪器留在原文件）。
 *
 * 初始装载后 agent 可能在会话中途保存草稿（save_workflow_draft →
 * turn-end 查询失效 → useWorkflowDraftQuery 重取）；画布必须跟上服务端
 * 真值而不是等到整页刷新——但绝不能覆盖用户未保存的编辑。前进判定以
 * 单调代次为准（#1226 复审 P2：Postgres 的 current_timestamp 固定在事务
 * 开始，并发写入按锁序落盘时更高代次可能带相同/更早的 updated_at），
 * 代次缺失（混合部署旧响应）才回落时间戳；时间戳仅用于展示与回落。
 * 用户编辑检测复用 useServerDraftApply 的 touched 跟踪。 */

/** 冲突通知/挂起记录的完整形状（#1226：generation 随冲突传播，保存层据
 * 此把 CAS 基线推进到服务端代次）。 */
export type ServerDraftConflictPayload = {
  yaml: string
  updatedAt: string
  hash: string | null
  generation: number | null
}

export type ServerDraftReapplyDecision =
  | { action: 'noop'; reason: 'not-ready' | 'same-or-older' | 'user-touched' }
  | { action: 'apply'; yaml: string; updatedAt: string }
  | ({ action: 'conflict' } & ServerDraftConflictPayload)

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

/** 服务端草稿是否前进（#1226 复审 P2）：以单调代次为准——Postgres 的
 * current_timestamp 固定在事务开始，并发写入按锁序落盘时更高代次可能带
 * 相同/更早的 updated_at，按时间戳会把新草稿误判成 same-or-older。任一侧
 * 代次缺失（混合部署旧响应）才回落时间戳比较。 */
export function isServerDraftAdvanced(
  serverGeneration: number | null | undefined,
  appliedGeneration: number | null | undefined,
  serverUpdatedAt: string | null | undefined,
  appliedUpdatedAt: string | null | undefined
): boolean {
  if (serverGeneration != null && appliedGeneration != null) {
    return serverGeneration > appliedGeneration
  }
  return isServerDraftNewer(serverUpdatedAt, appliedUpdatedAt)
}

/** 重应用决策：not-ready（查询未到达/基线未知）与 same-or-older（同一
 * 代次，或代次缺失时同一 updated_at/乱序迟到）都是 no-op；agent 草稿前进
 * 且用户未碰本地草稿 → apply（画布直接采用服务端真值）；用户有本地编辑 →
 * conflict（保留编辑，由保存层以 conflict 态向用户呈现，见
 * useWorkflowDraftPersistence）。
 * kimi review P1-1（幻影冲突）：用户自己的保存成功后，turn-end 重取回的
 * 正是本页刚存的草稿——内容与画布一致即 own-save 回显，静默推进基线
 * （apply 到相同内容 + 清 touched），不升起冲突警示。 */
export function decideServerDraftReapply(input: {
  serverDraftYaml: string | null | undefined
  serverDraftUpdatedAt: string | null | undefined
  serverDraftHash?: string | null
  /** #1226 五轮收口：服务端草稿代次——冲突通知携带（保存层恢复为 CAS 基线）；
   * 复审 P2：前进判定同样以它为准。 */
  serverDraftGeneration?: number | null
  appliedUpdatedAt: string | null | undefined
  /** #1226 复审 P2：已应用草稿的代次（前进判定的另一侧）。 */
  appliedGeneration?: number | null
  userTouched: boolean
  canvasYaml?: string
}): ServerDraftReapplyDecision {
  const {
    serverDraftYaml,
    serverDraftUpdatedAt,
    serverDraftHash,
    serverDraftGeneration,
    appliedUpdatedAt,
    appliedGeneration,
    userTouched,
    canvasYaml,
  } = input
  if (serverDraftYaml === undefined || serverDraftYaml === null) {
    return { action: 'noop', reason: 'not-ready' }
  }
  if (
    !isServerDraftAdvanced(
      serverDraftGeneration,
      appliedGeneration,
      serverDraftUpdatedAt,
      appliedUpdatedAt
    )
  ) {
    return { action: 'noop', reason: 'same-or-older' }
  }
  const base = { yaml: serverDraftYaml, updatedAt: serverDraftUpdatedAt ?? '' }
  // own-save 回显：服务端草稿即画布当前内容——不是外部变更，推进基线。
  if (canvasYaml !== undefined && serverDraftYaml === canvasYaml) {
    return { action: 'apply', ...base }
  }
  if (userTouched) {
    return {
      action: 'conflict',
      ...base,
      hash: serverDraftHash ?? null,
      generation: serverDraftGeneration ?? null,
    }
  }
  return { action: 'apply', ...base }
}
