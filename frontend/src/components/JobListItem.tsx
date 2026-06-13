import { useNavigate } from 'react-router-dom'
import { JOB_STATUS_LABELS } from '../labels'
import { formatRelativeTime } from '../helpers'
import type { JobRecord } from '../types'
import type { JobNodeSummary } from '../jobTypes'
import { JobNodeStepper } from './JobNodeStepper'
import styles from './JobListItem.module.css'

const TITLE_MAX_LEN = 30

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

function activeNodeKey(job: JobRecord): string | null {
  if (job.active_node_key) return job.active_node_key
  const running = job.node_summaries?.find(
    (n: JobNodeSummary) => n.status === 'running'
  )
  return running?.node_key ?? null
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
      title={`${job.title || job.source_id} · ${progressText(job)}`}
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
      </div>
      <div className={styles.nodeProgress}>
        <div className={styles.nodeProgressHeader}>
          <span className={styles.nodeProgressCount}>{progressText(job)}</span>
        </div>
        <JobNodeStepper
          nodeSummaries={job.node_summaries ?? []}
          activeNodeKey={activeNodeKey(job)}
        />
        {job.error_summary ? (
          <div className={styles.errorSummary}>{job.error_summary}</div>
        ) : null}
      </div>
      <span className={`${styles.badge} ${statusClass(job.status)}`}>
        {JOB_STATUS_LABELS[job.status] || job.status}
      </span>
      <span className={styles.time}>
        {job.created_at ? formatRelativeTime(job.created_at) : '—'}
      </span>
    </div>
  )
}
