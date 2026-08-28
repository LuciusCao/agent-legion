import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import { WorkflowInspectorDisclosure } from './WorkflowInspectorDisclosure'
import { EdgeList } from './WorkflowNodeInspectorLists'

export function WorkflowNodeDependencySection(props: {
  details: SelectedWorkflowNodeDetails
}) {
  const { node, incoming, outgoing } = props.details
  return (
    <WorkflowInspectorDisclosure
      title="依赖关系"
      summary={`${incoming.length} 入 / ${outgoing.length} 出`}
    >
      <EdgeList edges={incoming} nodeKey={node.key} outgoing={false} />
      <EdgeList edges={outgoing} nodeKey={node.key} outgoing />
    </WorkflowInspectorDisclosure>
  )
}
