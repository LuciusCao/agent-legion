import type { WorkflowDefinitionRecord } from '../../types'
import { WorkflowInspectorOverviewFallback } from './WorkflowInspectorOverviewFallback'
import { selectedNodeDetails } from './workflowStudioModel'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

export function WorkflowNodeInspector(props: Props) {
  const { workflow, selectedNodeKey } = props
  const details = selectedNodeDetails(workflow, selectedNodeKey)
  if (!workflow)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) return <WorkflowInspectorOverviewFallback workflow={workflow} />
  return (
    <WorkflowNodeInspectorBody
      details={details}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
    />
  )
}
