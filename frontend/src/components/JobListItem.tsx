import { useNavigate } from 'react-router-dom'
import { JOB_STATUS_LABELS } from '../labels'
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

function activeLabelClass(status: string): string {
  return status === 'running' ? styles.running : ''
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

function activeNodeSummary(job: JobRecord): JobNodeSummary | undefined {
  const key = activeNodeKey(job)
  if (!key) return undefined
  return job.node_summaries?.find((n) => n.node_key === key)
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

  const activeSummary = activeNodeSummary(job)
  const status = job.status

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
        <div className={styles.nodeProgressRow}>
          {activeSummary && (
            <span
              className={`${styles.activeLabel} ${activeLabelClass(status)}`}
              title={activeSummary.label}
            >
              {activeSummary.label}
            </span>
          )}
          <JobNodeStepper
            nodeSummaries={job.node_summaries ?? []}
            activeNodeKey={activeSummary?.node_key}
            totalNodes={job.total_nodes ?? 0}
          />
        </div>
        {job.error_summary ? (
          <div className={styles.errorSummary}>{job.error_summary}</div>
        ) : null}
      </div>
      <span className={`${styles.badge} ${statusClass(status)}`}>
        {JOB_STATUS_LABELS[job.status] || job.status}
      </span>
    </div>
  )
}
