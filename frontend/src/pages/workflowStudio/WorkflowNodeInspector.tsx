import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition } from '../../types/executorTypes'
import { WorkflowInspectorEmptyState } from './WorkflowInspectorOverviewFallback'
import { selectedNodeDetails } from './workflowStudioModel'
import { ghostDraftNodeDetails } from './workflowStudioGhostNode'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'
import { parseWorkflowKey } from './workflowStudioYamlDraft.parse'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  agentCatalog: AgentDefinition[]
  selectedNodeKey: string | null
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspector(props: Props) {
  const { workflow, selectedNodeKey } = props
  // 基线节点优先；基线里没有的（compare 叠加的 ghost 节点，如空态模板）从
  // 草稿 YAML 还原——draft-only 节点同样可编辑（start 由 sections 内部
  // 分流到只读契约段）。
  const details =
    selectedNodeDetails(workflow, selectedNodeKey) ??
    ghostDraftNodeDetails(props.definitionYaml, selectedNodeKey)
  if (!workflow && !details)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) return <WorkflowInspectorEmptyState />
  return (
    <WorkflowNodeInspectorBody
      details={details}
      agentCatalog={props.agentCatalog}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      workflowKey={
        workflow?.key ?? parseWorkflowKey(props.definitionYaml) ?? ''
      }
      readOnly={props.readOnly}
      onClose={props.onClose}
    />
  )
}
