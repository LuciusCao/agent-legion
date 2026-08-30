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
  // their node-code endpoints), so only the entry-contract section applies.
  if (node.node_type === 'start') {
    return <WorkflowNodeStartSection {...props} />
  }
  // capability 已有 published Agent 时，节点 YAML 的 config_schema 不参与
  // 解析（生效 schema 以 Agent 定义为准），Schema 编辑区改为指引文案。
  const agentBacked = props.agentCatalog.some(
    (definition) => definition.capability === node.capability
  )
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
        agentBacked={agentBacked}
        {...props}
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
