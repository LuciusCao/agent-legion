import type { JobSummary } from '../../types'
import styles from './JobRerunDialog.module.css'

export function JobRerunSelectionSummary({
  jobs,
  itemLabel,
  runnableJobs,
  notStartedJobs,
  runningJobs,
}: {
  jobs: JobSummary[]
  itemLabel: string
  runnableJobs: JobSummary[]
  notStartedJobs: JobSummary[]
  runningJobs: JobSummary[]
}) {
  return (
    <>
      {notStartedJobs.length > 0 && (
        <div className={styles.excludedBox}>
          {notStartedJobs.length} 个{itemLabel}
          尚未执行到所选节点，不能重跑。
        </div>
      )}
      {runningJobs.length > 0 && (
        <div className={styles.excludedBox}>
          {runningJobs.length} 个{itemLabel}正在运行，不能重跑。
        </div>
      )}
      <div className={styles.summary}>
        已选择 {jobs.length} 个{itemLabel}，可重跑 {runnableJobs.length} 个，
        {notStartedJobs.length} 个尚未执行到所选节点
      </div>
    </>
  )
}
