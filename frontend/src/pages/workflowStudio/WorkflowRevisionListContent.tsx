import type { WorkflowRevisionSummary } from '../../types'
import { WorkflowRevisionListItem } from './WorkflowRevisionListItem'
import containerStyles from './WorkflowRevisionListContainer.module.css'
import listStyles from './WorkflowRevisionList.module.css'

type Props = {
  revisions: WorkflowRevisionSummary[]
  activeRevisionId?: string
  selectedRevisionId?: string | null
  isLoadingRevision?: boolean
  revisionLoadError?: string | null
  onSelectRevision: (revisionId: string) => void
}

export function WorkflowRevisionListContent(props: Props) {
  if (props.revisions.length === 0) {
    return (
      <p className={listStyles.hash}>当前 workspace 还没有 workflow revision</p>
    )
  }
  return (
    <ul className={containerStyles.list}>
      {props.revisionLoadError && (
        <li className={containerStyles.error}>
          加载版本失败：{props.revisionLoadError}
        </li>
      )}
      {props.revisions.map((revision) => (
        <li key={revision.id}>
          <WorkflowRevisionListItem
            revision={revision}
            active={revision.id === props.activeRevisionId}
            selected={revision.id === props.selectedRevisionId}
            disabled={props.isLoadingRevision}
            onSelect={props.onSelectRevision}
          />
        </li>
      ))}
    </ul>
  )
}
