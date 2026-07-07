import type { JobFilterConfig, JobStatus } from '../stores/job/state'
import type { FilterCounts } from '../stores/job/selectors'
import { JOB_STATUS_LABELS } from '../labels'
import styles from './JobStatusFilterPanel.module.css'

const STATUS_OPTIONS: JobStatus[] = [
  'pending',
  'running',
  'completed',
  'failed',
  'paused',
]

export interface JobStatusFilterPanelProps {
  value: JobFilterConfig['status']
  counts: FilterCounts['status']
  onChange: (status: JobFilterConfig['status']) => void
}

export function JobStatusFilterPanel({
  value,
  counts,
  onChange,
}: JobStatusFilterPanelProps) {
  return (
    <div className={styles.panel} role="group" aria-label="任务状态过滤">
      <button
        type="button"
        className={`${styles.option} ${value === null ? styles.active : ''}`}
        onClick={() => onChange(null)}
        aria-pressed={value === null}
      >
        全部 <span className={styles.count}>({counts.all ?? 0})</span>
      </button>
      {STATUS_OPTIONS.map((status) => (
        <button
          key={status}
          type="button"
          className={`${styles.option} ${value === status ? styles.active : ''}`}
          onClick={() => onChange(status)}
          aria-pressed={value === status}
        >
          {JOB_STATUS_LABELS[status]}{' '}
          <span className={styles.count}>({counts[status] ?? 0})</span>
        </button>
      ))}
    </div>
  )
}
