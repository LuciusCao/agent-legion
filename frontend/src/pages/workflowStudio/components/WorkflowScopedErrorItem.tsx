import type { ScopedErrorItem } from '../workflowStudioErrors'
import styles from '../WorkflowValidationPanelErrors.module.css'

type Props = {
  item: ScopedErrorItem
}

export function WorkflowScopedErrorItem({ item }: Props) {
  return (
    <li className={styles.listItem}>
      <span>{item.message}</span>
      {item.nodeKey && (
        <button
          type="button"
          className={styles.nodeScope}
          onClick={item.onSelectNode}
        >
          节点: {item.nodeKey}
        </button>
      )}
      {!item.nodeKey && item.source && item.target && (
        <span className={styles.edgeScope}>
          {item.source} → {item.target}
        </span>
      )}
      {(item.line || item.column) && (
        <span className={styles.location}>
          位置: {item.line ?? '-'} 行 {item.column ?? '-'} 列
        </span>
      )}
    </li>
  )
}
