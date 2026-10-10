import { selectFilteredJobIds, useJobStore } from '../../stores/jobStore'
import { MaterialIcon } from '../MaterialIcon'
import { JobListVirtualized } from './JobListVirtualized'
import { JobListSkeleton } from './JobListSkeleton'
import { useEffectiveSelectedIds } from '../useEffectiveSelectedIds'
import styles from './JobList.module.css'

export function JobList({ workspaceId }: { workspaceId: string }) {
  const jobIds = useJobStore(selectFilteredJobIds)
  const selectedIds = useEffectiveSelectedIds(jobIds)
  const toggleSelect = useJobStore((state) => state.toggleSelect)
  const selectMode = useJobStore((state) => state.selectMode)
  const isLoading = useJobStore((state) => state.isLoading)
  const error = useJobStore((state) => state.error)
  if (error) {
    return (
      <div className={styles.error}>
        <MaterialIcon
          name="cloud_off"
          sx={{ fontSize: 48, color: 'text.secondary' }}
        />
        <p className="title-medium">任务列表加载失败</p>
        <p className={styles.errorMessage}>{error}</p>
        <p className={styles.errorHint}>刷新页面可重试</p>
      </div>
    )
  }
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
