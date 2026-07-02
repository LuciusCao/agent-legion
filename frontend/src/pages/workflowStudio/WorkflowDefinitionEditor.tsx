import styles from './WorkflowDefinitionEditor.module.css'

type Props = { value: string; onChange: (value: string) => void }

export function WorkflowDefinitionEditor({ value, onChange }: Props) {
  return (
    <>
      <label className={styles.editorLabel} htmlFor="workflow-definition">
        Workflow definition
      </label>
      <textarea
        id="workflow-definition"
        aria-label="Workflow definition"
        className={styles.yamlEditor}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={20}
      />
    </>
  )
}
