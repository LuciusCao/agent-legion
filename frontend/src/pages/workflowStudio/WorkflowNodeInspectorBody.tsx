import type { ExecutorDefinition } from '../../types/executorTypes'
import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'
import { WorkflowNodeDataContractSection } from './WorkflowNodeDataContractSection'
import { WorkflowNodeDependencySection } from './WorkflowNodeDependencySection'
import { WorkflowNodeEditorSection } from './WorkflowNodeEditorSection'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  executorCatalog: ExecutorDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
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
        <WorkflowNodeEditorSection
          node={node}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
        <WorkflowNodeExecutionSection
          node={node}
          executorCatalog={props.executorCatalog}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
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
      </div>
    </section>
  )
}
