import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import {
  switchWorkflowNodeToAgent,
  workflowNodeKindBadge,
} from '../shared/workflowStudioYamlDraft.nodeType'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspectorBody(props: Props) {
  const { node } = props.details
  // type=code 节点「切换为 Agent 执行」：改写草稿 YAML 的节点 type。
  const switchToAgent = () =>
    switchWorkflowNodeToAgent(
      props.definitionYaml,
      node.key,
      props.setDefinitionYaml
    )
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <WorkflowNodeInspectorHeader
        label={node.label}
        nodeKey={node.key}
        executorKind={workflowNodeKindBadge(node.node_type)}
        onClose={props.onClose}
      />
      <div className={styles.content}>
        <WorkflowNodeInspectorSections
          {...props}
          onSwitchToAgent={switchToAgent}
        />
      </div>
    </section>
  )
}
