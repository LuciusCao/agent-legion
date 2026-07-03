import type { WorkflowDefinitionRecord } from '../../../types'
import { patchWorkflowLabel } from '../workflowStudioYamlDraft'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

export function WorkflowMetadataEditor({
  workflow,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
  return (
    <section
      aria-label="Workflow metadata editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>结构化编辑</h3>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Workflow 名称</span>
        <input
          aria-label="Workflow 名称"
          className={styles.fieldInput}
          value={workflow.label}
          onChange={(event) =>
            onDefinitionYamlChange(
              patchWorkflowLabel(definitionYaml, event.target.value)
            )
          }
        />
      </label>
      <p className={styles.fieldHint}>
        Workflow Key 和 schema_version 暂不支持表单修改，请使用高级 YAML
        编辑器。
      </p>
    </section>
  )
}
