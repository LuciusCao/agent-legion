import type { VirtualItem } from '@tanstack/react-virtual'
import type { JobSummary } from '../types/jobTypes'
import { JobListItem } from './JobListItem'
import styles from './JobList.module.css'

interface JobListVirtualRowProps {
  job: JobSummary
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
