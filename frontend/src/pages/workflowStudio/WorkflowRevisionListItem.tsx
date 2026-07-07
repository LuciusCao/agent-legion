import type { WorkflowRevisionSummary } from '../../types'
import listStyles from './WorkflowRevisionList.module.css'
import itemStyles from './WorkflowRevisionListItem.module.css'

type Props = {
  revision: WorkflowRevisionSummary
  active: boolean
  selected: boolean
  onSelect: (revisionId: string) => void
}

export function WorkflowRevisionListItem({
  revision,
  active,
  selected,
  onSelect,
}: Props) {
  return (
    <button
      type="button"
      className={`${itemStyles.item} ${selected ? itemStyles.selected : ''}`}
      aria-pressed={selected}
      aria-current={active ? 'true' : undefined}
      onClick={() => onSelect(revision.id)}
    >
      <span className={listStyles.version}>v{revision.version}</span>
      <span className={listStyles.status}>
        {active ? 'active' : revision.status}
      </span>
      <span className={listStyles.hash}>
        {revision.definition_hash.slice(0, 8)}
      </span>
    </button>
  )
}
