import type { WorkflowDefinitionRecord } from '../../types'
import {
  isBranchNode,
  isEntryNode,
  isTerminalNode,
} from './workflowStudioTopology'

export function topologyBadges(
  workflow: WorkflowDefinitionRecord,
  nodeKey: string
): Array<'start' | 'entry' | 'branch' | 'terminal'> {
  const node = workflow.nodes.find((item) => item.key === nodeKey)
  if (node?.node_type === 'start') {
    // start 是契约入口（EXEC-WORKFLOW-START-001），用专属徽标与普通 entry 区分。
    return ['start']
  }
  return [
    ...(isEntryNode(workflow, nodeKey) ? (['entry'] as const) : []),
    ...(isBranchNode(workflow, nodeKey) ? (['branch'] as const) : []),
    ...(isTerminalNode(workflow, nodeKey) ? (['terminal'] as const) : []),
  ]
}
