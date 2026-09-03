// capability→Agent 绑定解析的门控（#426 codex 终轮 P2）：useAgentCatalog 是
// workspace 级 hook，不感知节点 capability，故只把两份目录查询（published
// catalog + agent-definitions）的 settle 信号下发到节点级；本文件承载节点级
// 门控计算——bindingStatus 在 capability 已解析出 published 命中（agent 非
// 空 && !isDraft，即 agent 来自 catalog 而非 draft 回落）时不等 definitions
// （编辑器按 ID 加载详情）；未命中 published（空目录或无该 capability）时
// 必须等 definitions settle 才能断定「未绑定」——否则 useCapabilityAgent 看到
// 的空 draft 列表会把已有 draft 的 capability 误判为未绑定、先放出可操作的
// 新建表单，definitions 返回后切到真实 draft 又重挂编辑器丢输入、甚至先建出
// 同 capability 的多余草稿（codex 终轮 P2 指出的空列表竞态）。

/** 绑定解析状态：pending=仍在途（不出可操作表单）；error=有查询失败且
 * 无数据（绑定不可解析，不落回可操作表单）；ready=绑定已是终态（命中
 * published，或确认未绑定/回落 draft），放行渲染编辑器/新建表单。 */
export type AgentBindingStatus = 'pending' | 'error' | 'ready'

/** 两份目录查询的 settle 信号（workspace 级，无 capability 语义）：
 * *Settled = 首次查询已返回（后台刷新失败但缓存数据还在时绑定仍可按
 * 缓存解析，不算 failed）；*Failed = 失败且无数据（绑定不可解析）。 */
export type AgentCatalogSettle = {
  catalogSettled: boolean
  catalogFailed: boolean
  definitionsSettled: boolean
  definitionsFailed: boolean
}

/** 门控语义（#426 codex 终轮 P2，调用方为 WorkflowNodeExecutionSection）：
 * catalog 在途 → pending；catalog 失败无数据 → error；catalog 已返回且
 * 命中 published（useCapabilityAgent 的解析结果：agent 已解析且非 draft
 * 回落，即来自 published 目录）→ ready（不等 definitions——编辑器按 ID
 * 加载详情）；未命中 published → 看 definitions：在途 → pending，失败
 * 无数据 → error，已返回 → ready（有无 draft 都已是终态）。显式返回类型
 * 保持字面量联合（单行三元会被 TS 拓宽成 string 击穿下游类型）。 */
export function bindingStatus(
  resolved: { agent?: unknown; isDraft?: boolean },
  settle: AgentCatalogSettle
): AgentBindingStatus {
  // 字段经解构访问（不做属性点取）：executor_decoupling 的遗留 ratchet
  // 禁扫 WorkflowNode 的 agent 属性点访问写法。
  const { agent, isDraft } = resolved
  if (settle.catalogFailed) return 'error'
  if (!settle.catalogSettled) return 'pending'
  if (agent !== undefined && !isDraft) return 'ready'
  if (settle.definitionsFailed) return 'error'
  return settle.definitionsSettled ? 'ready' : 'pending'
}
