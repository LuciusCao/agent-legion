import { JOB_SOURCE_TYPE_LABELS } from '../labels'
import type { JobRecord } from '../types'
import { jobWorkflowVersionText } from '../lib/jobWorkflowVersion'
import styles from './JobListItem.module.css'

export function JobListItemDescription({ job }: { job: JobRecord }) {
  const versionText = jobWorkflowVersionText(job)
  return (
    <div className={styles.description}>
      {JOB_SOURCE_TYPE_LABELS[job.source_type] ?? job.source_type} ·{' '}
      {job.source_id}
      {versionText ? (
        <>
          {' · '}
          <span
            className={job.is_workflow_outdated ? styles.outdatedVersion : ''}
          >
            {versionText}
          </span>
        </>
      ) : null}
      {job.error_summary ? (
        <>
          {' · '}
          <span className={styles.errorText} title={job.error_summary}>
            {job.error_summary}
          </span>
        </>
      ) : null}
    </div>
  )
}
