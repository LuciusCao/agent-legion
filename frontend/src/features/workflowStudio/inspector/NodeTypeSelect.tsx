import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'
import styles from './WorkflowNodeInspectorHeader.module.css'

// 头部类型选择器本体（#392）：三类型原生 select（与 inspector 现有原生
// select 约定一致）；当前类型不可切换（start / readOnly 徽标态）时由
// Header 降级渲染，本组件只处理可切换形态。
export function NodeTypeSelect(props: {
  nodeType: SwitchableNodeType
  onChange: (type: SwitchableNodeType) => void
}) {
  const labels: Record<SwitchableNodeType, string> = {
    code: 'Code',
    agent: 'Agent',
    approval: '审批门',
  }
  return (
    <select
      aria-label="节点类型"
      className={styles.typeSelect}
      value={props.nodeType}
      onChange={(event) =>
        props.onChange(event.target.value as SwitchableNodeType)
      }
    >
      {(Object.keys(labels) as SwitchableNodeType[]).map((type) => (
        <option key={type} value={type}>
          {labels[type]}
        </option>
      ))}
    </select>
  )
}
