import styles from './WorkflowNodeInspector.module.css'

export function WorkflowInspectorEmptyState() {
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <h2 className={styles.title}>选择一个节点</h2>
      <p className={styles.empty}>点击画布中的节点，在这里查看和编辑配置。</p>
    </section>
  )
}
