import { JOB_SOURCE_TYPE_LABELS } from '../../labels'
import type { JobSummary } from '../../types/jobTypes'
import { WorkflowVersionChip } from '../WorkflowVersionChip'
import { clientTokenHint } from './JobClientTokenSource'
import styles from './JobListItem.module.css'

export function JobListItemDescription({ job }: { job: JobSummary }) {
  return (
    <div className={styles.description}>
      {JOB_SOURCE_TYPE_LABELS[job.source_type] ?? job.source_type} ·{' '}
      {job.client_token ? (
        // #925：列表信息密度不变，只在悬停提示里显式给出 client_token。
        <span
          title={`client_token：${job.client_token} · ${clientTokenHint(job)}`}
        >
          {job.source_id}
        </span>
      ) : (
        job.source_id
      )}
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
