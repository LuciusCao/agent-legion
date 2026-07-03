import type { WorkflowDefinitionRecord } from '../../../types'
import { WorkflowMetadataEditor } from './WorkflowMetadataEditor'
import styles from '../WorkflowNodeInspector.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

export function WorkflowNodeInspectorOverview({
  workflow,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
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
      <WorkflowMetadataEditor
        workflow={workflow}
        definitionYaml={definitionYaml}
        onDefinitionYamlChange={onDefinitionYamlChange}
      />
    </section>
  )
}
