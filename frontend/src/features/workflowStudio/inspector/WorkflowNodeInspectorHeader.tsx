import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { IconButton, Tooltip } from '@mui/material'
import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'
import styles from './WorkflowNodeInspectorHeader.module.css'

// 节点类型标签（#392 选择器 + 徽标共用）：code 为默认执行形态。
export const NODE_TYPE_LABELS: Record<SwitchableNodeType, string> = {
  code: 'Code',
  agent: 'Agent',
  approval: '审批门',
}

type Props = {
  label: string
  nodeKey: string
  /** 当前节点显式类型；start 传 'start'（不可切换，只读展示）。 */
  nodeType: string | undefined
  onClose: () => void
  /** 类型切换：改写草稿 YAML（含字段清洗）。readOnly 时不传，头部退化
   * 为类型徽标。 */
  onNodeTypeChange?: (type: SwitchableNodeType) => void
}

export function WorkflowNodeInspectorHeader(props: Props) {
  const isSwitchableType =
    props.nodeType === 'code' ||
    props.nodeType === 'agent' ||
    props.nodeType === 'approval'
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        <h2 className={styles.title}>{props.label}</h2>
        {isSwitchableType && props.onNodeTypeChange ? (
          <select
            aria-label="节点类型"
            className={styles.typeSelect}
            value={props.nodeType}
            onChange={(event) => {
              const change = props.onNodeTypeChange
              change?.(event.target.value as SwitchableNodeType)
            }}
          >
            {(Object.keys(NODE_TYPE_LABELS) as SwitchableNodeType[]).map(
              (type) => (
                <option key={type} value={type}>
                  {NODE_TYPE_LABELS[type]}
                </option>
              )
            )}
          </select>
        ) : (
          <span className={styles.kind}>{props.nodeType ?? 'code'}</span>
        )}
      </div>
      <Tooltip title="关闭节点配置">
        <IconButton
          size="small"
          aria-label="关闭节点配置"
          onClick={props.onClose}
        >
          <ChevronRightIcon />
        </IconButton>
      </Tooltip>
      <div className={styles.key} title={props.nodeKey}>
        {props.nodeKey}
      </div>
    </header>
  )
}
