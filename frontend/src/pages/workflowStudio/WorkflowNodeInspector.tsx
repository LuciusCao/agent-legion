import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition } from '../../types/executorTypes'
import { WorkflowInspectorEmptyState } from './WorkflowInspectorOverviewFallback'
import {
  ghostStartNodeDetails,
  selectedNodeDetails,
} from './workflowStudioModel'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'
import { WorkflowNodeStartSection } from './WorkflowNodeStartSection'

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
  const details = selectedNodeDetails(workflow, selectedNodeKey)
  if (details)
    return (
      <WorkflowNodeInspectorBody
        details={details}
        agentCatalog={props.agentCatalog}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        workflowKey={workflow?.key ?? ''}
        readOnly={props.readOnly}
        onClose={props.onClose}
      />
    )
  // 基线里没有该节点：compare 叠加的 ghost 节点（新 workspace 空态模板）。
  // start 节点是只读契约，从草稿 YAML 还原展示；其余 ghost 节点走空态。
  const ghostDetails = ghostStartNodeDetails(
    props.definitionYaml,
    selectedNodeKey
  )
  if (ghostDetails)
    return (
      <section aria-label="Workflow inspector">
        <WorkflowNodeStartSection details={ghostDetails} />
      </section>
    )
  if (!workflow)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  return <WorkflowInspectorEmptyState />
}
