import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { JobRecord } from '../types'
import { JobListVirtualRow } from './JobListVirtualRow'
import styles from './JobList.module.css'

interface JobListVirtualizedProps {
  jobs: JobRecord[]
  selectedIds: Set<string>
  selectMode: boolean
  workspaceId: string
  onToggleSelect: (jobId: string) => void
}

export function JobListVirtualized({
  jobs,
  selectedIds,
  selectMode,
  workspaceId,
  onToggleSelect,
}: JobListVirtualizedProps) {
  const parentRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual is known to be safe here
  const rowVirtualizer = useVirtualizer({
    count: jobs.length,
    estimateSize: () => 76,
    getScrollElement: () => parentRef.current,
    overscan: 8,
  })
  return (
    <div ref={parentRef} className={styles.list} role="list">
      <div
        style={{
          height: `${rowVirtualizer.getTotalSize()}px`,
          position: 'relative',
          width: '100%',
        }}
      >
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const job = jobs[virtualRow.index]
          if (!job) return null
          return (
            <JobListVirtualRow
              key={job.id}
              job={job}
              selected={selectedIds.has(job.id)}
              selectMode={selectMode}
              virtualRow={virtualRow}
              workspaceId={workspaceId}
              onToggleSelect={() => onToggleSelect(job.id)}
            />
          )
        })}
      </div>
    </div>
  )
}
