import editorStyles from './WorkflowStructuredEditor.module.css'
import { WorkflowRuntimeOptionsDatalist } from './WorkflowRuntimeOptionsDatalist'

/**
 * 节点 execution 输入框。options 非空时挂 datalist——在线 Worker 实际声明的
 * provider/model，自由输入仍然可用；空值不继承 workflow 顶层配置。
 */
export function WorkflowRuntimeField(props: {
  label: string
  value: string
  options?: string[]
  listId?: string
  readOnly?: boolean
  /** M48：label 尾部的通道状态点（已修改 · 待保存 / 已生效）。 */
  labelSuffix?: React.ReactNode
  onChange: (value: string) => void
}) {
  return (
    <label className={editorStyles.field}>
      <span className={editorStyles.fieldLabel}>
        {props.label}
        {props.labelSuffix}
      </span>
      <input
        aria-label={props.label}
        className={editorStyles.fieldInput}
        value={props.value}
        disabled={props.readOnly}
        placeholder="未配置"
        list={props.listId}
        onChange={(event) => props.onChange(event.target.value)}
      />
      <WorkflowRuntimeOptionsDatalist
        id={props.listId}
        options={props.options}
      />
      <span className={editorStyles.fieldHint}>
        仅作用于当前节点；空值 = 未配置。
      </span>
    </label>
  )
}
