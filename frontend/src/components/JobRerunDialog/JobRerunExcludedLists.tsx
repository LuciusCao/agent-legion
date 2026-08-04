import type { JobSummary } from '../../types'
import styles from './JobRerunDialog.module.css'

function jobName(job: JobSummary) {
  return job.source_id || job.title || job.id
}

export function JobRerunExcludedLists({
  failedMode,
  effectiveNodeKey,
  nonFailedJobs,
  excluded,
}: {
  failedMode: boolean
  effectiveNodeKey: string | null
  nonFailedJobs: JobSummary[]
  excluded: JobSummary[]
}) {
  const jobs = failedMode ? nonFailedJobs : excluded
  if (jobs.length === 0 || (!failedMode && !effectiveNodeKey)) return null
  return (
    <div className={styles.excludedBox}>
      <div className={styles.excludedTitle}>
        {failedMode
          ? '以下任务未失败，将被跳过：'
          : '以下任务不包含所选节点，将被跳过：'}
      </div>
      <ul className={styles.excludedList}>
        {jobs.map((job) => (
          <li key={job.id}>{jobName(job)}</li>
        ))}
      </ul>
    </div>
  )
}
