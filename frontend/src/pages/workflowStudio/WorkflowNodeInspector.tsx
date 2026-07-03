import type { WorkflowDefinitionRecord } from '../../types'
import { selectedNodeDetails } from './workflowStudioModel'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'
import { WorkflowNodeInspectorOverview } from './components/WorkflowNodeInspectorOverview'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

export function WorkflowNodeInspector({
  workflow,
  selectedNodeKey,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
  const details = selectedNodeDetails(workflow, selectedNodeKey)
  if (!workflow)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) {
    return (
      <WorkflowNodeInspectorOverview
        workflow={workflow}
        definitionYaml={definitionYaml}
        onDefinitionYamlChange={onDefinitionYamlChange}
      />
    )
  }
  return (
    <WorkflowNodeInspectorBody
      details={details}
      definitionYaml={definitionYaml}
      onDefinitionYamlChange={onDefinitionYamlChange}
    />
  )
}
