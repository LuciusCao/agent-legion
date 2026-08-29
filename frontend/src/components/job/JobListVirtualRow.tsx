import { memo } from 'react'
import type { JobSummary } from '../../types/jobTypes'
import { JobListItem } from './JobListItem'
import styles from './JobList.module.css'

interface JobListVirtualRowProps {
  job: JobSummary
  selected: boolean
  selectMode: boolean
  virtualRowStart: number
  workspaceId: string
  onToggleSelect: (jobId: string) => void
}

export const JobListVirtualRow = memo(function JobListVirtualRow({
  job,
  selected,
  selectMode,
  virtualRowStart,
  workspaceId,
  onToggleSelect,
}: JobListVirtualRowProps) {
  return (
    <div
      className={styles.item}
      role="listitem"
      style={{ transform: `translateY(${virtualRowStart}px)` }}
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
})
