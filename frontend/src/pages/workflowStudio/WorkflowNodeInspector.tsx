import type { WorkflowDefinitionRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { WorkflowInspectorEmptyState } from './WorkflowInspectorOverviewFallback'
import { selectedNodeDetails } from './workflowStudioModel'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  executorCatalog: ExecutorDefinition[]
  selectedNodeKey: string | null
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspector(props: Props) {
  const { workflow, selectedNodeKey } = props
  const details = selectedNodeDetails(workflow, selectedNodeKey)
  if (!workflow)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) return <WorkflowInspectorEmptyState />
  return (
    <WorkflowNodeInspectorBody
      details={details}
      executorCatalog={props.executorCatalog}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
      onClose={props.onClose}
    />
  )
}
