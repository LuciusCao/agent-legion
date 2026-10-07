import type { WorkflowDefinitionRecord } from '../types'

type WorkflowNode = WorkflowDefinitionRecord['nodes'][number]

/**
 * 进入隐含 code 池的节点（P-0.5）：节点级并发上限只对它们有意义。#1079
 * （#440 P3b）：自含 agent 节点没有（或只有冻结的、已不展示的）路由行，
 * agent 判定以显式 node_type 为准；路由行只兜底 node_type 缺失的旧记录。
 * agentRoutes 与 node_limits 都按 workspace 取回，节点只按 node_key 匹配
 * （#211 M3 退役了 workflow_key 维度）。
 */
export function codePoolNodes(
  nodes: readonly WorkflowNode[],
  agentRoutes: readonly { node_key: string }[]
): WorkflowNode[] {
  const agentRouted = new Set(agentRoutes.map((route) => route.node_key))
  return nodes.filter(
    (node) => node.node_type !== 'agent' && !agentRouted.has(node.key)
  )
}
