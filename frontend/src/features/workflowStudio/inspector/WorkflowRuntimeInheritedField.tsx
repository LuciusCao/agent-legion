import editorStyles from './WorkflowStructuredEditor.module.css'
import { WorkflowRuntimeOptionsDatalist } from './WorkflowRuntimeOptionsDatalist'

/**
 * 节点 execution 输入框：空值继承 workflow 顶层 execution 默认（schema v64
 * 起 workspace 级默认已退役）。options 非空时挂 datalist——在线 Worker
 * 实际声明的 provider/model，自由输入仍然可用。
 */
export function WorkflowRuntimeInheritedField(props: {
  label: string
  value: string
  inherited: string
  options?: string[]
  listId?: string
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
        placeholder="继承 workflow 默认"
        list={props.listId}
        onChange={(event) => props.onChange(event.target.value)}
      />
      <WorkflowRuntimeOptionsDatalist
        id={props.listId}
        options={props.options}
      />
      <span className={editorStyles.fieldHint}>
        {props.value ? '覆盖 workflow 默认' : '继承 workflow 默认'}：
        {props.inherited || '未配置'}
      </span>
    </label>
  )
}
