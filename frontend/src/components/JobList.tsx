import { useEffect } from 'react'
import { useJobStore } from '../stores/jobStore'
import { JobListItem } from './JobListItem'
import { ExpandedJobPanel } from './ExpandedJobPanel'
import styles from './JobList.module.css'

export interface JobListProps {
  workspaceId: string
}

export function JobList({ workspaceId }: JobListProps) {
  const jobs = useJobStore((state) => state.getFilteredJobs())
  const selectedIds = useJobStore((state) => state.selectedIds)
  const expandedId = useJobStore((state) => state.expandedId)
  const fetchJobs = useJobStore((state) => state.fetchJobs)
  const toggleSelect = useJobStore((state) => state.toggleSelect)
  const toggleExpand = useJobStore((state) => state.toggleExpand)

  useEffect(() => {
    fetchJobs(workspaceId)
  }, [workspaceId, fetchJobs])

  if (jobs.length === 0) {
    return (
      <div className={styles.empty}>
        <md-icon
          style={{ fontSize: '48px', color: 'var(--md-sys-color-outline)' }}
        >
          inbox
        </md-icon>
        <p className="title-medium">暂无任务</p>
      </div>
    )
  }

  return (
    <div className={styles.list} role="list">
      {jobs.map((job) => {
        const isExpanded = expandedId === job.id
        return (
          <div key={job.id} className={styles.item} role="listitem">
            <JobListItem
              job={job}
              selected={selectedIds.has(job.id)}
              expanded={isExpanded}
              onToggleSelect={() => toggleSelect(job.id)}
              onToggleExpand={() => toggleExpand(job.id)}
            />
            {isExpanded && (
              <ExpandedJobPanel
                job={job}
                workspaceId={workspaceId}
                onViewDetail={() => {
                  // navigation is handled inside ExpandedJobPanel
                }}
                onRerun={() => {
                  // Placeholder: trigger rerun in Phase 3
                }}
                onRunTo={() => {
                  // Placeholder: open run-to dialog in Phase 3
                }}
                onDelete={() => {
                  // Placeholder: trigger delete in Phase 3
                }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
