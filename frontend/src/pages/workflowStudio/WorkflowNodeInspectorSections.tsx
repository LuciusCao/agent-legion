import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../types/executorTypes'
import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { WorkflowNodeCodeSection } from './WorkflowNodeCodeSection'
import { WorkflowNodeConfigSection } from './WorkflowNodeConfigSection'
import { WorkflowNodeDataContractSection } from './WorkflowNodeDataContractSection'
import { WorkflowNodeDependencySection } from './WorkflowNodeDependencySection'
import { WorkflowNodeEditorSection } from './WorkflowNodeEditorSection'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'

export type InspectorSectionProps = {
  details: SelectedWorkflowNodeDetails
  executorCatalog: ExecutorDefinition[]
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// Composition of the inspector sections: revision-scoped YAML editors first,
// then workspace-scoped node state (code module + node config, both applied
// immediately outside the draft/publish flow), then read-only structure.
export function WorkflowNodeInspectorSections(props: InspectorSectionProps) {
  const { node } = props.details
  return (
    <>
      <WorkflowNodeEditorSection
        node={node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeExecutionSection
        node={node}
        executorCatalog={props.executorCatalog}
        agentCatalog={props.agentCatalog}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeCodeSection
        key={`code-${node.key}`}
        node={node}
        executorCatalog={props.executorCatalog}
        readOnly={props.readOnly}
      />
      <WorkflowNodeConfigSection
        key={`config-${node.key}`}
        node={node}
        readOnly={props.readOnly}
      />
      <WorkflowNodeDataContractSection
        key={`data-contract-${node.key}`}
        node={node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeDependencySection
        key={`dependencies-${node.key}`}
        details={props.details}
      />
    </>
  )
}
