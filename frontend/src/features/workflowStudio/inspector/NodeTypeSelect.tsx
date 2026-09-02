import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'
import { isSwitchableNodeType } from './nodeTypeSelector'
import styles from './WorkflowNodeInspectorHeader.module.css'

const TYPE_LABELS: Record<SwitchableNodeType, string> = {
  code: 'Code',
  agent: 'Agent',
  approval: '审批门',
}

// 头部类型选择器（#392）：三类型原生 select（与 inspector 现有原生
// select 约定一致）。不可切换形态（start / readOnly 无回调）由 Header
// 降级为徽标，本组件只处理可切换形态。
export function NodeTypeSelect(props: {
  nodeType: SwitchableNodeType
  onChange: (type: SwitchableNodeType) => void
}) {
  return (
    <select
      aria-label="节点类型"
      className={styles.typeSelect}
      value={props.nodeType}
      onChange={(e) => props.onChange(e.target.value as SwitchableNodeType)}
    >
      {(Object.keys(TYPE_LABELS) as SwitchableNodeType[]).map((type) => (
        <option key={type} value={type}>
          {TYPE_LABELS[type]}
        </option>
      ))}
    </select>
  )
}

// 头部类型位（#392）：可切换且可写时渲染选择器，否则只读徽标。
export function HeaderNodeTypeSlot(props: {
  nodeType: string | undefined
  onNodeTypeChange?: (type: SwitchableNodeType) => void
}) {
  const nodeType = props.nodeType
  const change = props.onNodeTypeChange
  if (isSwitchableNodeType(nodeType) && change) {
    return <NodeTypeSelect nodeType={nodeType} onChange={change} />
  }
  return <span className={styles.kind}>{nodeType ?? 'code'}</span>
}
