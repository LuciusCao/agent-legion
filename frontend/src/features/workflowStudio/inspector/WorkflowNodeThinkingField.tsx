import editorStyles from './WorkflowStructuredEditor.module.css'

/** Thinking 档位选择：空值由该节点 runtime 决定，不继承 workflow 配置。 */
export function WorkflowNodeThinkingField(props: {
  value: string
  readOnly?: boolean
  /** M48：label 尾部的通道状态点（已修改 · 待保存 / 已生效）。 */
  labelSuffix?: React.ReactNode
  onChange: (value: string) => void
}) {
  return (
    <label className={editorStyles.field}>
      <span className={editorStyles.fieldLabel}>
        Thinking
        {props.labelSuffix}
      </span>
      <select
        aria-label="Thinking"
        className={editorStyles.fieldInput}
        value={props.value}
        disabled={props.readOnly}
        onChange={(event) => props.onChange(event.target.value)}
      >
        <option value="">runtime 决定</option>
        <option value="low">Low</option>
        <option value="medium">Medium</option>
        <option value="high">High</option>
      </select>
    </label>
  )
}
