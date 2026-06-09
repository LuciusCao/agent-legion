import { JOB_STATUS_LABELS } from '../labels'
import { formatRelativeTime } from '../helpers'
import type { JobRecord } from '../types'
import styles from './JobListItem.module.css'

export interface JobListItemProps {
  job: JobRecord
  selected: boolean
  expanded: boolean
  onToggleSelect: () => void
  onToggleExpand: () => void
}

function statusClass(status: string): string {
  switch (status) {
    case 'running':
      return styles.running
    case 'completed':
      return styles.completed
    case 'failed':
      return styles.failed
    case 'pending':
    case 'queued':
    default:
      return styles.pending
  }
}

function progressText(job: JobRecord): string {
  const completed = job.completed_nodes ?? 0
  const total = job.total_nodes ?? 0
  if (total <= 0) return '—'
  return `${completed}/${total}`
}

function progressPercent(job: JobRecord): number {
  const completed = job.completed_nodes ?? 0
  const total = job.total_nodes ?? 0
  if (total <= 0) return 0
  return Math.min(100, Math.max(0, (completed / total) * 100))
}

export function JobListItem({
  job,
  selected,
  expanded,
  onToggleSelect,
  onToggleExpand,
}: JobListItemProps) {
  return (
    <div className={styles.row} data-job={job.id}>
      <input
        type="checkbox"
        className={styles.checkbox}
        checked={selected}
        onChange={onToggleSelect}
        onClick={(e) => e.stopPropagation()}
        aria-label={`选择任务 ${job.source_id}`}
      />
      <div className={styles.main}>
        <div className={styles.sourceId}>{job.source_id}</div>
        <div className={styles.title}>{job.title || '—'}</div>
      </div>
      <span className={`${styles.badge} ${statusClass(job.status)}`}>
        {JOB_STATUS_LABELS[job.status] || job.status}
      </span>
      <span className={styles.time}>
        {job.created_at ? formatRelativeTime(job.created_at) : '—'}
      </span>
      <div className={styles.progress}>
        <div className={styles.progressTrack}>
          <div
            className={styles.progressFill}
            style={{ width: `${progressPercent(job)}%` }}
            data-progress={progressPercent(job)}
          />
        </div>
        <span className={styles.progressLabel}>{progressText(job)}</span>
      </div>
      <button
        type="button"
        className={styles.expandBtn}
        onClick={onToggleExpand}
        aria-expanded={expanded}
      >
        {expanded ? '收起 ▲' : '展开 ▼'}
      </button>
    </div>
  )
}
