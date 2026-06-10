import { useNavigate } from 'react-router-dom'
import { JOB_STATUS_LABELS } from '../labels'
import { formatRelativeTime } from '../helpers'
import type { JobRecord } from '../types'
import styles from './JobListItem.module.css'

const TITLE_MAX_LEN = 30
const STEM_MAX_LEN = 60

export interface JobListItemProps {
  job: JobRecord
  selected: boolean
  selectMode: boolean
  onToggleSelect: () => void
  workspaceId?: string
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
  selectMode,
  onToggleSelect,
  workspaceId,
}: JobListItemProps) {
  const navigate = useNavigate()

  const handleRowClick = () => {
    if (workspaceId) {
      navigate(`/workspaces/${workspaceId}/jobs/${job.id}`)
    }
  }

  return (
    <div
      className={styles.row}
      data-job={job.id}
      onClick={handleRowClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          handleRowClick()
        }
      }}
    >
      {selectMode && (
        <input
          type="checkbox"
          className={styles.checkbox}
          checked={selected}
          onChange={(e) => {
            e.stopPropagation()
            onToggleSelect()
          }}
          onClick={(e) => e.stopPropagation()}
          aria-label={`选择任务 ${job.source_id}`}
        />
      )}
      <div className={styles.main}>
        <div className={styles.sourceId}>
          {job.title
            ? `${job.title.length > TITLE_MAX_LEN ? job.title.slice(0, TITLE_MAX_LEN) + '…' : job.title} - ${job.source_id}`
            : job.source_id}
        </div>
        {job.stem && (
          <div className={styles.stem}>
            {job.stem.length > STEM_MAX_LEN
              ? job.stem.slice(0, STEM_MAX_LEN) + '…'
              : job.stem}
          </div>
        )}
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
    </div>
  )
}
