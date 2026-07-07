import type { WorkflowRevisionSummary } from '../../types'
import styles from './WorkflowRevisionList.module.css'

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
      className={`${styles.item} ${selected ? styles.selected : ''}`}
      aria-pressed={selected}
      aria-current={active ? 'true' : undefined}
      onClick={() => onSelect(revision.id)}
    >
      <span className={styles.version}>v{revision.version}</span>
      <span className={styles.status}>
        {active ? 'active' : revision.status}
      </span>
      <span className={styles.hash}>
        {revision.definition_hash.slice(0, 8)}
      </span>
    </button>
  )
}
