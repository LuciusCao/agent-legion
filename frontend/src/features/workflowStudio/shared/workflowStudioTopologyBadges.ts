import type { TopologyBadge } from '../../../components/dag/dagNodeTypes'
import type { WorkflowDefinitionRecord } from '../../../types'
import {
  isBranchNode,
  isEntryNode,
  isTerminalNode,
} from './workflowStudioTopology'

// 节点徽标（#392 Phase 3）：拓扑徽标（entry/branch/terminal）与节点类型
// 正交；start = 契约入口专属（EXEC-WORKFLOW-START-001，无拓扑）；
// approval = 审批门专属（EXEC-APPROVAL-001）叠加拓扑。
export function topologyBadges(
  workflow: WorkflowDefinitionRecord,
  nodeKey: string
): TopologyBadge[] {
  const node = workflow.nodes.find((item) => item.key === nodeKey)
  const hits: Array<[TopologyBadge, boolean]> = [
    ['entry', isEntryNode(workflow, nodeKey)],
    ['branch', isBranchNode(workflow, nodeKey)],
    ['terminal', isTerminalNode(workflow, nodeKey)],
  ]
  const suffix = hits.filter(([, hit]) => hit).map(([badge]) => badge)
  if (node?.node_type === 'start') return ['start']
  if (node?.node_type === 'approval') return ['approval', ...suffix]
  return suffix
}
