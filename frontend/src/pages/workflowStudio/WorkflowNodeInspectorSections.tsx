import type { AgentDefinition } from '../../types/executorTypes'
import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { WorkflowNodeCodeSection } from './WorkflowNodeCodeSection'
import { WorkflowNodeConfigSection } from './WorkflowNodeConfigSection'
import { WorkflowNodeDataContractSection } from './WorkflowNodeDataContractSection'
import { WorkflowNodeDependencySection } from './WorkflowNodeDependencySection'
import { WorkflowNodeEditorSection } from './WorkflowNodeEditorSection'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'
import { WorkflowNodeStartSection } from './WorkflowNodeStartSection'

export type InspectorSectionProps = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  workflowKey: string
  readOnly?: boolean
}

// Composition of the inspector sections: revision-scoped YAML editors first,
// then workspace-scoped node state (code module + node config, both applied
// immediately outside the draft/publish flow), then read-only structure.
export function WorkflowNodeInspectorSections(props: InspectorSectionProps) {
  const { node } = props.details
  // Start nodes carry the entry contract (type: start) and never execute:
  // the capability/execution/code editors do not apply (the backend 404s
  // their node-code endpoints), so show the read-only contract instead.
  if (node.node_type === 'start') {
    return <WorkflowNodeStartSection details={props.details} />
  }
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
        agentCatalog={props.agentCatalog}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        workflowKey={props.workflowKey}
        readOnly={props.readOnly}
      />
      <WorkflowNodeCodeSection
        key={`code-${node.key}`}
        node={node}
        agentCatalog={props.agentCatalog}
        workflowKey={props.workflowKey}
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
