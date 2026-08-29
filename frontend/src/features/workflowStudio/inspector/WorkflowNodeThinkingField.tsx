import editorStyles from './WorkflowStructuredEditor.module.css'

/** Thinking 档位选择：空值继承 workflow 顶层 execution 默认（自
 * WorkflowNodeRuntimeSettings 拆出，文件预算）。 */
export function WorkflowNodeThinkingField(props: {
  value: string
  inherited: string
  readOnly?: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className={editorStyles.field}>
      <span className={editorStyles.fieldLabel}>Thinking</span>
      <select
        aria-label="Thinking"
        className={editorStyles.fieldInput}
        value={props.value}
        disabled={props.readOnly}
        onChange={(event) => props.onChange(event.target.value)}
      >
        <option value="">
          {props.inherited
            ? `继承 workflow 默认（${props.inherited}）`
            : 'runtime 决定'}
        </option>
        <option value="low">Low</option>
        <option value="medium">Medium</option>
        <option value="high">High</option>
      </select>
    </label>
  )
}
