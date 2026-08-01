import { useJobStore } from '../stores/jobStore'
import { selectFilteredJobIds } from '../stores/job/selectors'
import { MaterialIcon } from './MaterialIcon'
import { JobListVirtualized } from './JobListVirtualized'
import { JobListSkeleton } from './JobListSkeleton'
import { useEffectiveSelectedIds } from './useEffectiveSelectedIds'
import styles from './JobList.module.css'

export function JobList({ workspaceId }: { workspaceId: string }) {
  const jobIds = useJobStore(selectFilteredJobIds)
  const selectedIds = useEffectiveSelectedIds(jobIds)
  const toggleSelect = useJobStore((state) => state.toggleSelect)
  const selectMode = useJobStore((state) => state.selectMode)
  const isLoading = useJobStore((state) => state.isLoading)
  if (isLoading) return <JobListSkeleton />
  if (jobIds.length === 0) {
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
      jobIds={jobIds}
      selectedIds={selectedIds}
      selectMode={selectMode}
      workspaceId={workspaceId}
      onToggleSelect={toggleSelect}
    />
  )
}
