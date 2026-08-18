import type { AgentDefinition } from '../../types/executorTypes'
import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  workflowKey: string
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspectorBody(props: Props) {
  const { node } = props.details
  // P-0.5：无 Agent 路由的节点一律进入隐含 code 池。
  const isAgent = props.agentCatalog.some(
    (definition) => definition.capability === node.capability
  )
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <WorkflowNodeInspectorHeader
        label={node.label}
        nodeKey={node.key}
        executorKind={isAgent ? '' : 'code'}
        onClose={props.onClose}
      />
      <div className={styles.content}>
        <WorkflowNodeInspectorSections {...props} />
      </div>
    </section>
  )
}
