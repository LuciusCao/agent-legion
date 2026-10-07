import type { AgentListItem, WorkflowDefinitionRecord } from '../../types'
import type { components } from '../../generated/api'

export type WorkflowNode = WorkflowDefinitionRecord['nodes'][number]
export type AgentProvenanceEntry =
  components['schemas']['WorkspaceAgentProvenanceEntry']

/** capability → 仍按 capability 路由到 Agent 定义的 legacy agent 节点
 * （#1079：只有未自含——没有 execution.runtime——的 `type: agent` 节点
 * 还回读 Agent 定义；自含节点与 code 节点同名 capability 不构成引用）。 */
export function legacyAgentNodeReferences(
  nodes: readonly WorkflowNode[]
): Map<string, WorkflowNode[]> {
  const byCapability = new Map<string, WorkflowNode[]>()
  for (const node of nodes) {
    if (node.node_type !== 'agent' || !node.capability) continue
    if (node.execution?.runtime) continue
    const list = byCapability.get(node.capability) ?? []
    list.push(node)
    byCapability.set(node.capability, list)
  }
  return byCapability
}

/** agent_id → 当前 active revision 中内联了它的节点（#1079，#440 D1）。 */
export function inlinedNodesByAgent(
  entries: readonly AgentProvenanceEntry[]
): Map<string, AgentProvenanceEntry[]> {
  const byAgent = new Map<string, AgentProvenanceEntry[]>()
  for (const entry of entries) {
    const list = byAgent.get(entry.agent_id) ?? []
    list.push(entry)
    byAgent.set(entry.agent_id, list)
  }
  return byAgent
}

/** 从未发布过的 Agent（列表行是草稿且没有已发布版本）。 */
export function isDraftOnly(agent: AgentListItem): boolean {
  return agent.status === 'draft' && agent.published_capability == null
}

/**
 * 引用判定口径（#906）：legacy agent 节点按 capability 路由到 **已发布
 * 版本**，而列表行是最新版本（可能是改了 capability 的草稿）。故有已发布
 * 版本时一律按已发布 capability 判定；从未发布的才看草稿。
 */
export function routedCapability(agent: AgentListItem): string {
  return agent.published_capability ?? agent.capability
}

function labelled(key: string, label: string | null | undefined): string {
  return label && label !== key ? `${label}（${key}）` : key
}

export function nodeName(node: WorkflowNode): string {
  return labelled(node.key, node.label)
}

export function inlinedNodeName(entry: AgentProvenanceEntry): string {
  return labelled(entry.node_key, entry.node_label)
}
