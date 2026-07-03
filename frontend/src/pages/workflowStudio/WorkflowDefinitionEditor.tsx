import styles from './WorkflowDefinitionEditor.module.css'

type Props = { value: string; onChange: (value: string) => void }

export function WorkflowDefinitionEditor({ value, onChange }: Props) {
  return (
    <>
      <label className={styles.editorLabel} htmlFor="workflow-definition">
        高级 YAML 编辑器
      </label>
      <textarea
        id="workflow-definition"
        aria-label="高级 YAML 编辑器"
        className={styles.yamlEditor}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={20}
      />
    </>
  )
}
