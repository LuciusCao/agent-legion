import type { WorkflowRevisionSummary } from '../../types'
import { WorkflowRevisionListItem } from './WorkflowRevisionListItem'
import containerStyles from './WorkflowRevisionListContainer.module.css'
import listStyles from './WorkflowRevisionList.module.css'

type Props = {
  revisions: WorkflowRevisionSummary[]
  activeRevisionId?: string
  selectedRevisionId?: string | null
  onSelectRevision: (revisionId: string) => void
}

export function WorkflowRevisionListContent({
  revisions,
  activeRevisionId,
  selectedRevisionId,
  onSelectRevision,
}: Props) {
  if (revisions.length === 0) {
    return (
      <p className={listStyles.hash}>当前 workspace 还没有 workflow revision</p>
    )
  }
  return (
    <ul className={containerStyles.list}>
      {revisions.map((revision) => (
        <li key={revision.id}>
          <WorkflowRevisionListItem
            revision={revision}
            active={revision.id === activeRevisionId}
            selected={revision.id === selectedRevisionId}
            onSelect={onSelectRevision}
          />
        </li>
      ))}
    </ul>
  )
}
