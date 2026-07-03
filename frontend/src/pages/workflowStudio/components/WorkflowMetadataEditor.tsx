import type { WorkflowDefinitionRecord } from '../../../types'
import {
  parseWorkflowLabel,
  patchWorkflowLabel,
} from '../workflowStudioYamlDraft'
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
  const draftLabel = parseWorkflowLabel(definitionYaml) ?? workflow.label
  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowLabel(definitionYaml, event.target.value)
    )
  return (
    <div className={styles.structuredSection}>
      <h3 className={styles.structuredTitle}>结构化编辑</h3>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Workflow 名称</span>
        <input
          aria-label="Workflow 名称"
          className={styles.fieldInput}
          value={draftLabel}
          onChange={handleChange}
        />
      </label>
      <p className={styles.fieldHint}>
        Key 与 schema_version 请改用 YAML 编辑器。
      </p>
    </div>
  )
}
