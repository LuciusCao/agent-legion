import { JOB_SOURCE_TYPE_LABELS } from '../labels'
import type { JobRecord } from '../types'
import { WorkflowVersionChip } from './WorkflowVersionChip'
import styles from './JobListItem.module.css'

export function JobListItemDescription({ job }: { job: JobRecord }) {
  return (
    <div className={styles.description}>
      {JOB_SOURCE_TYPE_LABELS[job.source_type] ?? job.source_type} ·{' '}
      {job.source_id}
      {job.workflow_version != null ? (
        <>
          {' · '}
          <WorkflowVersionChip job={job} />
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
