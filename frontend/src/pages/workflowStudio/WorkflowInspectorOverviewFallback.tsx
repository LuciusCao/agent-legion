import type { WorkflowDefinitionRecord } from '../../types'
import styles from './WorkflowNodeInspector.module.css'

export function WorkflowInspectorOverviewFallback({
  workflow,
}: {
  workflow: WorkflowDefinitionRecord
}) {
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <h2 className={styles.title}>工作流概览</h2>
      <OverviewItem label="节点" value={workflow.nodes.length} />
      <OverviewItem label="连线" value={workflow.edges.length} />
      <OverviewItem label="接入模式" value={workflow.intake.modes.length} />
    </section>
  )
}

function OverviewItem(props: { label: string; value: number }) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>{props.label}</div>
      <div className={styles.value}>{props.value}</div>
    </div>
  )
}
