import type { VirtualItem } from '@tanstack/react-virtual'
import type { JobRecord } from '../types'
import { JobListItem } from './JobListItem'
import styles from './JobList.module.css'

interface JobListVirtualRowProps {
  job: JobRecord
  selected: boolean
  selectMode: boolean
  virtualRow: VirtualItem
  workspaceId: string
  onToggleSelect: () => void
}

export function JobListVirtualRow({
  job,
  selected,
  selectMode,
  virtualRow,
  workspaceId,
  onToggleSelect,
}: JobListVirtualRowProps) {
  return (
    <div
      className={styles.item}
      role="listitem"
      style={{ transform: `translateY(${virtualRow.start}px)` }}
    >
      <JobListItem
        job={job}
        selected={selected}
        selectMode={selectMode}
        onToggleSelect={onToggleSelect}
        workspaceId={workspaceId}
      />
    </div>
  )
}
