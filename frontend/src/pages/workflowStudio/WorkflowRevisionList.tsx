import type { WorkflowRevisionSummary } from '../../types'
import { WorkflowRevisionListContent } from './WorkflowRevisionListContent'
import styles from './WorkflowRevisionList.module.css'

type Props = {
  revisions: WorkflowRevisionSummary[]
  activeRevisionId?: string
  selectedRevisionId?: string | null
  onSelectRevision: (revisionId: string) => void
}

export function WorkflowRevisionList(props: Props) {
  return (
    <section aria-label="Workflow revisions" className={styles.section}>
      <h2 className={styles.title}>版本</h2>
      <WorkflowRevisionListContent {...props} />
    </section>
  )
}
