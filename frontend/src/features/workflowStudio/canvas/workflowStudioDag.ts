import type {
  DagGraphEdge,
  DagGraphNode,
} from '../../../components/dag/DagGraph'
import type { WorkflowDefinitionRecord } from '../../../types'
import { conditionLabel } from '../shared/workflowStudioModel'
import { topologyBadges } from './workflowStudioDagBadges'
import { nodeExecutionWarning } from './workflowStudioExecutionWarnings'
import {
  resolveStudioNodeRouting,
  type StudioNodeRouting,
} from '../shared/workflowStudioRouting'

export function buildDagNodes(
  workflow: WorkflowDefinitionRecord | null,
  routing?: StudioNodeRouting
): DagGraphNode[] {
  // executionWarning（#333）：agent 节点有效 execution 缺 provider/model 时
  // 注入警告文案（记录侧已合并顶层 execution 默认，节点值即有效值）。
  // 保留 workflow?. 可选链形态：回调内 workflow 的非空收窄依赖它。
  return (
    workflow?.nodes.map((node) => ({
      key: node.key,
      label: node.label,
      status: 'pending',
      created_at: '',
      capability: node.capability,
      ...resolveStudioNodeRouting(node, routing),
      topologyBadges: topologyBadges(workflow, node.key),
      terminalOutcome: node.terminal?.outcome,
      executionWarning: nodeExecutionWarning(node),
      inputs: node.inputs,
      outputs: node.outputs,
    })) ?? []
  )
}

export function buildDagEdges(
  workflow: WorkflowDefinitionRecord | null
): DagGraphEdge[] {
  return (workflow?.edges ?? []).map((edge) => ({
    from: edge.source,
    to: edge.target,
    label: conditionLabel(edge.condition),
    conditional: Boolean(edge.condition),
  }))
}
