import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../types/executorTypes'
import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  executorCatalog: ExecutorDefinition[]
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  workflowKey: string
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspectorBody(props: Props) {
  const { node } = props.details
  const executorKind =
    findCapabilityBindings(props.executorCatalog, node.capability)[0]?.executor
      .kind ?? ''
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <WorkflowNodeInspectorHeader
        label={node.label}
        nodeKey={node.key}
        executorKind={executorKind}
        onClose={props.onClose}
      />
      <div className={styles.content}>
        <WorkflowNodeInspectorSections {...props} />
      </div>
    </section>
  )
}
