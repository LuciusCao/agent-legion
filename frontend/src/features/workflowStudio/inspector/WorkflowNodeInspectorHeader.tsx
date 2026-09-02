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

// 切到 approval 会剥掉的字段（确认文案用；与 patchWorkflowNodeType 的
// APPROVAL_FORBIDDEN_FIELDS 镜像清单保持同步）。
const APPROVAL_SWITCH_WARNING =
  '切换为审批门将清除该节点的 capability、execution、skill、' +
  'shard/reduce、config_schema 与审批白名单以外的 config，且不可撤销' +
  '（草稿历史可在 workflow-draft 版本中回退）。确定切换吗？'

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
  function handleChange(type: SwitchableNodeType) {
    if (type === props.nodeType) return
    // 切到 approval 是破坏性清洗（P1：设计稿 §4 要求确认文案明示清除
    // 内容；草稿自动保存，误选即覆盖）。取消时把 select 值弹回原类型。
    if (type === 'approval' && !window.confirm(APPROVAL_SWITCH_WARNING)) {
      return
    }
    props.onNodeTypeChange?.(type)
  }
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        <h2 className={styles.title}>{props.label}</h2>
        {isSwitchableType && props.onNodeTypeChange ? (
          <select
            aria-label="节点类型"
            className={styles.typeSelect}
            value={props.nodeType}
            onChange={(event) => handleChange(event.target.value as SwitchableNodeType)}
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
