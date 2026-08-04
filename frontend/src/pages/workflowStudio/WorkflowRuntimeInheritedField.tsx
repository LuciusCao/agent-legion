import editorStyles from './components/WorkflowStructuredEditor.module.css'

export function WorkflowRuntimeInheritedField(props: {
  label: string
  value: string
  inherited: string
  readOnly?: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className={editorStyles.field}>
      <span className={editorStyles.fieldLabel}>{props.label}</span>
      <input
        aria-label={props.label}
        className={editorStyles.fieldInput}
        value={props.value}
        disabled={props.readOnly}
        placeholder="继承全局设置"
        onChange={(event) => props.onChange(event.target.value)}
      />
      <span className={editorStyles.fieldHint}>
        {props.value ? '覆盖全局' : '继承全局'}：{props.inherited || '未配置'}
      </span>
    </label>
  )
}
