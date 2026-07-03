import type { WorkflowRevisionSummary } from '../../../types'
import styles from './WorkflowPublishReviewDialog.module.css'

type Props = {
  workflowKey: string | null
  activeRevision: WorkflowRevisionSummary | null
  nextVersion: number
  definitionHash: string | null
}

export function WorkflowPublishReviewDialogMeta({
  workflowKey,
  activeRevision,
  nextVersion,
  definitionHash,
}: Props) {
  return (
    <div className={styles.metaGrid}>
      <div className={styles.metaRow}>
        <span className={styles.metaLabel}>版本</span>
        <span className={styles.metaValue}>
          当前 active v{activeRevision?.version ?? '-'} → 新 revision v
          {nextVersion}
        </span>
      </div>
      <div className={styles.metaRow}>
        <span className={styles.metaLabel}>Workflow Key</span>
        <span className={styles.metaValue}>{workflowKey ?? '-'}</span>
      </div>
      <div className={styles.metaRow}>
        <span className={styles.metaLabel}>Definition Hash</span>
        <span className={styles.metaValue}>
          {definitionHash?.slice(0, 8) ?? '--------'}
        </span>
      </div>
    </div>
  )
}
