import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { IconButton, Tooltip } from '@mui/material'
import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'
import { NodeTypeSelect } from './NodeTypeSelect'
import { isSwitchableNodeType } from './nodeTypeSelector'
import styles from './WorkflowNodeInspectorHeader.module.css'

type Props = {
  label: string
  nodeKey: string
  /** 当前显式类型；start 只读展示徽标，不进选择器。 */
  nodeType: string | undefined
  onClose: () => void
  /** 类型切换；readOnly 不传即退化徽标。 */
  onNodeTypeChange?: (type: SwitchableNodeType) => void
}

export function WorkflowNodeInspectorHeader(props: Props) {
  const nodeType = props.nodeType
  const switchable = isSwitchableNodeType(nodeType) ? nodeType : null
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        <h2 className={styles.title}>{props.label}</h2>
        {switchable && props.onNodeTypeChange ? (
          <NodeTypeSelect
            nodeType={switchable}
            onChange={props.onNodeTypeChange}
          />
        ) : (
          <span className={styles.kind}>{nodeType ?? 'code'}</span>
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
