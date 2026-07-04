import type { JobRecord, JobSummary } from '../types'
import { MaterialIcon } from './MaterialIcon'
import styles from './WorkflowVersionChip.module.css'

type WorkflowVersionChipJob = Pick<
  JobRecord | JobSummary,
  | 'workflow_version'
  | 'current_workflow_revision_version'
  | 'is_workflow_outdated'
>

export interface WorkflowVersionChipProps {
  job: WorkflowVersionChipJob
}

export function WorkflowVersionChip({ job }: WorkflowVersionChipProps) {
  if (job.workflow_version == null) return null
  const current = job.current_workflow_revision_version
  const outdated =
    job.is_workflow_outdated &&
    current != null &&
    current !== job.workflow_version
  return (
    <span className={styles.chip + (outdated ? ' ' + styles.outdated : '')}>
      <MaterialIcon name="account_tree" />
      <span className={styles.text}>v{job.workflow_version}</span>
      {outdated ? (
        <>
          <MaterialIcon name="arrow_circle_up" />
          <span className={styles.text}>v{current}</span>
        </>
      ) : null}
    </span>
  )
}
