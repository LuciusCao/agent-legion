import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { WorkflowNodeEditorSection } from './WorkflowNodeEditorSection'
import { EdgeList, ItemList } from './WorkflowNodeInspectorLists'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// WorkflowNodeInspectorBody is currently display-only: it lists node metadata
// and edges. readOnly is accepted and forwarded so future structured edit
// inputs can be wired with disabled={readOnly} / readOnly={readOnly}.
export function WorkflowNodeInspectorBody(props: Props) {
  const { details, readOnly } = props
  const { node, incoming, outgoing } = details
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <h2 className={styles.title}>{node.label}</h2>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>标识</div>
        <div className={styles.value}>{node.key}</div>
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>能力</div>
        <div className={styles.value}>{node.capability}</div>
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          输入产物 ({node.inputs.length})
        </div>
        <ItemList items={node.inputs} />
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          输出产物 ({node.outputs.length})
        </div>
        <ItemList items={node.outputs} />
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>上游</div>
        <EdgeList edges={incoming} nodeKey={node.key} outgoing={false} />
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>下游</div>
        <EdgeList edges={outgoing} nodeKey={node.key} outgoing={true} />
      </div>
      {node.terminal && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Terminal Outcome</div>
          <span className={styles.outcome}>{node.terminal.outcome}</span>
        </div>
      )}
      <WorkflowNodeEditorSection
        node={node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={readOnly}
      />
    </section>
  )
}
