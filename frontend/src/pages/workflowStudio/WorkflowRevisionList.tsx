import type { WorkflowRevisionSummary } from '../../types'
import { WorkflowRevisionListContent } from './WorkflowRevisionListContent'
import containerStyles from './WorkflowRevisionListContainer.module.css'

type Props = {
  revisions: WorkflowRevisionSummary[]
  activeRevisionId?: string
  selectedRevisionId?: string | null
  isLoadingRevision?: boolean
  revisionLoadError?: string | null
  onSelectRevision: (revisionId: string) => void
}

export function WorkflowRevisionList(props: Props) {
  return (
    <section
      aria-label="Workflow revisions"
      className={containerStyles.section}
    >
      <h2 className={containerStyles.title}>版本</h2>
      <WorkflowRevisionListContent {...props} />
    </section>
  )
}
