import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { IconButton, Tooltip } from '@mui/material'
import styles from './WorkflowNodeInspectorHeader.module.css'

export function WorkflowNodeInspectorHeader(props: {
  label: string
  nodeKey: string
  executorKind: string
  onClose: () => void
}) {
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        <h2 className={styles.title}>{props.label}</h2>
        {props.executorKind && (
          <span className={styles.kind}>{props.executorKind}</span>
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
