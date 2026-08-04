import type { WorkflowDefinitionRecord } from '../../types'
import {
  isBranchNode,
  isEntryNode,
  isTerminalNode,
} from './workflowStudioTopology'

export function topologyBadges(
  workflow: WorkflowDefinitionRecord,
  nodeKey: string
): Array<'entry' | 'branch' | 'terminal'> {
  return [
    ...(isEntryNode(workflow, nodeKey) ? (['entry'] as const) : []),
    ...(isBranchNode(workflow, nodeKey) ? (['branch'] as const) : []),
    ...(isTerminalNode(workflow, nodeKey) ? (['terminal'] as const) : []),
  ]
}
