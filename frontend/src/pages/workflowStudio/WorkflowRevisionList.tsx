import type { WorkflowRevisionSummary } from '../../types'
import styles from './WorkflowRevisionList.module.css'
type Props = { revisions: WorkflowRevisionSummary[]; activeRevisionId?: string }

export function WorkflowRevisionList({ revisions, activeRevisionId }: Props) {
  return (
    <section aria-label="Workflow revisions" className={styles.section}>
      <h2 className={styles.title}>版本</h2>
      {revisions.length === 0 ? (
        <p className={styles.hash}>当前 workspace 还没有 workflow revision</p>
      ) : (
        <ul className={styles.list}>
          {revisions.map((revision) => (
            <li
              key={revision.id}
              className={styles.item}
              aria-current={
                revision.id === activeRevisionId ? 'true' : undefined
              }
            >
              <span className={styles.version}>v{revision.version}</span>
              <span className={styles.status}>{revision.status}</span>
              <span className={styles.hash}>
                {revision.definition_hash.slice(0, 8)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
