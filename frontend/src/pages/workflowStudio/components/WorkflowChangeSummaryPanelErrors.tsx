import { groupCompareErrors } from '../workflowStudioChanges'
import styles from './WorkflowChangeSummaryPanel.module.css'
import type { components } from '../../../generated/api'

type CompareError = components['schemas']['WorkflowDraftCompareError']

type Props = {
  errors: CompareError[]
  onSelectNode?: (nodeKey: string) => void
}

export function WorkflowChangeSummaryPanelErrors({
  errors,
  onSelectNode,
}: Props) {
  const groups = groupCompareErrors(errors)
  return (
    <>
      {groups.map((group) => (
        <div key={group.category} className={styles.errorGroup}>
          <h4 className={styles.errorGroupTitle}>{group.categoryLabel}</h4>
          <ul className={styles.list}>
            {group.errors.map((error, index) => (
              <li
                key={`${group.category}-${index}`}
                className={styles.errorItem}
              >
                <span className={styles.errorMessage}>{error.message}</span>
                {error.node_key && onSelectNode && (
                  <button
                    type="button"
                    className={styles.clickableNode}
                    onClick={() => onSelectNode(error.node_key!)}
                  >
                    节点: {error.node_key}
                  </button>
                )}
                {(error.line || error.column) && (
                  <span className={styles.errorLocation}>
                    位置: {error.line ?? '-'} 行 {error.column ?? '-'} 列
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  )
}
