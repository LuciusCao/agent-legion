import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useJobStore } from '../stores/jobStore'
import { JobListVirtualRowById } from './JobListVirtualRowById'
import { useJobListLoadMore } from './useJobListLoadMore'
import styles from './JobList.module.css'

interface JobListVirtualizedProps {
  jobIds: string[]
  selectedIds: Set<string>
  selectMode: boolean
  workspaceId: string
  onToggleSelect: (jobId: string) => void
}

export function JobListVirtualized({
  jobIds,
  selectedIds,
  selectMode,
  workspaceId,
  onToggleSelect,
}: JobListVirtualizedProps) {
  const parentRef = useRef<HTMLDivElement>(null)
  const loadingMore = useJobStore((state) => state.loadingMore)
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual is known to be safe here
  const rowVirtualizer = useVirtualizer({
    count: jobIds.length,
    estimateSize: () => 76,
    getScrollElement: () => parentRef.current,
    overscan: 8,
  })
  const virtualItems = rowVirtualizer.getVirtualItems()
  const lastIndex = virtualItems[virtualItems.length - 1]?.index ?? -1
  useJobListLoadMore(workspaceId, jobIds.length, lastIndex)
  return (
    <div ref={parentRef} className={styles.list} role="list">
      <div
        style={{
          height: `${rowVirtualizer.getTotalSize()}px`,
          position: 'relative',
          width: '100%',
        }}
      >
        {virtualItems.map((virtualRow) => {
          const jobId = jobIds[virtualRow.index]
          if (!jobId) return null
          return (
            <JobListVirtualRowById
              key={jobId}
              jobId={jobId}
              selected={selectedIds.has(jobId)}
              selectMode={selectMode}
              virtualRow={virtualRow}
              workspaceId={workspaceId}
              onToggleSelect={() => onToggleSelect(jobId)}
            />
          )
        })}
      </div>
      {loadingMore && <div className={styles.loadingMore}>加载中…</div>}
    </div>
  )
}
