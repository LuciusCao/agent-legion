import { useEffect } from 'react'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { JobListItem } from './JobListItem'
import styles from './JobList.module.css'

export interface JobListProps {
  workspaceId: string
}

export function JobList({ workspaceId }: JobListProps) {
  const jobs = useJobStore((state) => state.getFilteredJobs())
  const selectedIds = useJobStore((state) => state.selectedIds)
  const fetchJobs = useJobStore((state) => state.fetchJobs)
  const toggleSelect = useJobStore((state) => state.toggleSelect)
  const selectMode = useJobStore((state) => state.selectMode)

  useEffect(() => {
    fetchJobs(workspaceId)
  }, [workspaceId, fetchJobs])

  const entity = useWorkspaceStore(
    (state) => state.currentWorkspace?.default_entity || 'question'
  )

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
      {jobs.map((job) => (
        <div key={job.id} className={styles.item} role="listitem">
          <JobListItem
            job={job}
            selected={selectedIds.has(job.id)}
            selectMode={selectMode}
            onToggleSelect={() => toggleSelect(job.id)}
            workspaceId={workspaceId}
            entity={entity}
          />
        </div>
      ))}
    </div>
  )
}
