import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import { useNodeTypeSwitch } from './useNodeTypeSwitch'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import type { AgentCatalogSettle } from './agentBindingStatus'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  agentCatalogSettle: AgentCatalogSettle
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspectorBody(props: Props) {
  const { node } = props.details
  // 头部类型选择器（#392）接线；start 只读展示，不下发回调。
  const changeNodeType = useNodeTypeSwitch(
    props.definitionYaml,
    node.key,
    props.setDefinitionYaml
  )
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <WorkflowNodeInspectorHeader
        label={node.label}
        nodeKey={node.key}
        nodeType={node.node_type}
        onNodeTypeChange={props.readOnly ? undefined : changeNodeType}
        onClose={props.onClose}
      />
      <div className={styles.content}>
        <WorkflowNodeInspectorSections {...props} />
      </div>
    </section>
  )
}
