import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import { WorkflowNodeCodeSection } from '../code-editor/WorkflowNodeCodeSection'
import { WorkflowNodeConfigSchemaSection } from './WorkflowNodeConfigSchemaSection'
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
  /** type=code 节点「切换为 Agent 执行」（改写草稿 YAML type；Body 注入）。 */
  onSwitchToAgent?: () => boolean
  readOnly?: boolean
}

// Composition of the inspector sections: revision-scoped YAML editors first,
// then workspace-scoped node state (code module + node config, both applied
// immediately outside the draft/publish flow), then read-only structure.
export function WorkflowNodeInspectorSections(props: InspectorSectionProps) {
  const { node } = props.details
  // Start nodes carry the entry contract (type: start) and never execute:
  // the capability/execution/code editors do not apply (the backend 404s
  // their node-code endpoints), so only the entry-contract section applies.
  if (node.node_type === 'start') {
    return <WorkflowNodeStartSection {...props} />
  }
  return (
    <>
      <WorkflowNodeEditorSection
        node={node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeConfigSchemaSection
        key={`config-schema-${node.key}`}
        node={node}
        {...props}
      />
      <WorkflowNodeExecutionSection node={node} {...props} />
      <WorkflowNodeCodeSection
        key={`code-${node.key}`}
        node={node}
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
