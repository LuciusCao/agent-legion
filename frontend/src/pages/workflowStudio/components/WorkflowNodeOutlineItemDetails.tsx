import type { WorkflowNodeRecord } from '../../../types'
import styles from '../WorkflowNodeOutlineItem.module.css'

type Props = {
  node: WorkflowNodeRecord
}

export function WorkflowNodeOutlineItemDetails({ node }: Props) {
  return (
    <>
      <span className={styles.meta}>
        {node.key} · {node.capability}
      </span>
      {node.terminal?.outcome && (
        <span className={styles.outcome}>{node.terminal.outcome}</span>
      )}
    </>
  )
}
