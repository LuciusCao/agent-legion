import styles from './WorkflowDefinitionEditor.module.css'
import type { WorkflowDefinitionEditorProps as Props } from './WorkflowDefinitionEditor.types'

export function WorkflowDefinitionEditor({
  value,
  onChange,
  readOnly = false,
  label = '高级 YAML 编辑器',
}: Props) {
  return (
    <>
      <label className={styles.editorLabel} htmlFor="workflow-definition">
        {label}
      </label>
      <textarea
        id="workflow-definition"
        aria-label={label}
        className={styles.yamlEditor}
        value={value}
        readOnly={readOnly}
        rows={20}
        onChange={(event) => onChange(event.target.value)}
      />
    </>
  )
}
