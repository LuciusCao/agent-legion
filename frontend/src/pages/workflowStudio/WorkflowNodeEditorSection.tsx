import type { WorkflowNodeRecord } from '../../types'
import { WorkflowNodeStructuredEditor } from './components/WorkflowNodeStructuredEditor'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

export function WorkflowNodeEditorSection({
  node,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  if (readOnly) return null
  return (
    <WorkflowNodeStructuredEditor
      node={node}
      definitionYaml={definitionYaml}
      onDefinitionYamlChange={setDefinitionYaml}
    />
  )
}
