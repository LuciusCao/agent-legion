import type { AgentListItem, WorkflowDefinitionRecord } from '../../types'

export type WorkflowNode = WorkflowDefinitionRecord['nodes'][number]

/** capability → 引用它的 agent 节点（只有 `type: agent` 节点按 capability
 * 路由到 Agent；code 节点同名 capability 不构成引用）。 */
export function agentNodeReferences(
  nodes: readonly WorkflowNode[]
): Map<string, WorkflowNode[]> {
  const byCapability = new Map<string, WorkflowNode[]>()
  for (const node of nodes) {
    if (node.node_type !== 'agent' || !node.capability) continue
    const list = byCapability.get(node.capability) ?? []
    list.push(node)
    byCapability.set(node.capability, list)
  }
  return byCapability
}

/** 从未发布过的 Agent（列表行是草稿且没有已发布版本）。 */
export function isDraftOnly(agent: AgentListItem): boolean {
  return agent.status === 'draft' && agent.published_capability == null
}

/**
 * 引用判定口径（#906）：active revision 的 agent 节点按 capability 路由到
 * **已发布版本**，而列表行是最新版本（可能是改了 capability 的草稿）。故
 * 有已发布版本时一律按已发布 capability 判定；从未发布的才看草稿。
 */
export function routedCapability(agent: AgentListItem): string {
  return agent.published_capability ?? agent.capability
}

/** 草稿 capability 与已发布 capability 不同时返回草稿的值，否则 null。 */
export function pendingDraftCapability(agent: AgentListItem): string | null {
  if (agent.status !== 'draft' || isDraftOnly(agent)) return null
  return agent.capability !== routedCapability(agent) ? agent.capability : null
}

export function nodeName(node: WorkflowNode): string {
  return node.label && node.label !== node.key
    ? `${node.label}（${node.key}）`
    : node.key
}

export function referencedWarning(
  agent: AgentListItem,
  refs: readonly WorkflowNode[]
): string {
  const names = refs.map(nodeName).join('、')
  if (isDraftOnly(agent)) {
    return `该 Agent 从未发布，但其草稿 capability 被当前 workflow 的 ${refs.length} 个节点使用（${names}）。这些节点目前解析不到它；归档后仍需另行发布对应的 Agent。`
  }
  return `该 Agent 的已发布版本仍被当前 workflow 的 ${refs.length} 个节点引用（${names}）。后端不会阻止归档，但归档会连同已发布版本一起归档，之后这些节点的 capability 将没有已发布的 Agent 可解析，运行与再次发布 workflow 都可能因此失败。`
}
