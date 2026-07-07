import type { WorkflowDefinitionRecord } from '../../types'
import { selectedNodeDetails } from './workflowStudioModel'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  readOnly?: boolean
}

export function WorkflowNodeInspector(props: Props) {
  const { workflow, selectedNodeKey, readOnly } = props
  const details = selectedNodeDetails(workflow, selectedNodeKey)
  if (!workflow)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) {
    return (
      <section aria-label="Workflow inspector" className={styles.panel}>
        <h2 className={styles.title}>工作流概览</h2>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>节点</div>
          <div className={styles.value}>{workflow.nodes.length}</div>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>连线</div>
          <div className={styles.value}>{workflow.edges.length}</div>
        </div>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>接入模式</div>
          <div className={styles.value}>{workflow.intake.modes.length}</div>
        </div>
      </section>
    )
  }
  return <WorkflowNodeInspectorBody details={details} readOnly={readOnly} />
}
