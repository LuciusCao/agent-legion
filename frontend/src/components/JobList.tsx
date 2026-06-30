import { useJobStore } from '../stores/jobStore'
import { MaterialIcon } from './MaterialIcon'
import { JobListVirtualized } from './JobListVirtualized'
import { JobListSkeleton } from './JobListSkeleton'
import styles from './JobList.module.css'

export function JobList({ workspaceId }: { workspaceId: string }) {
  const jobs = useJobStore((state) => state.getFilteredJobs())
  const selectedIds = useJobStore((state) => state.selectedIds)
  const toggleSelect = useJobStore((state) => state.toggleSelect)
  const selectMode = useJobStore((state) => state.selectMode)
  const isLoading = useJobStore((state) => state.isLoading)
  if (isLoading) return <JobListSkeleton />
  if (jobs.length === 0) {
    return (
      <div className={styles.empty}>
        <MaterialIcon
          name="inbox"
          sx={{ fontSize: 48, color: 'text.secondary' }}
        />
        <p className="title-medium">暂无任务</p>
      </div>
    )
  }

  return (
    <JobListVirtualized
      jobs={jobs}
      selectedIds={selectedIds}
      selectMode={selectMode}
      workspaceId={workspaceId}
      onToggleSelect={toggleSelect}
    />
  )
}
