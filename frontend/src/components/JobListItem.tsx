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

function activeLabelClass(nodeStatus: string): string {
  return nodeStatus === 'running' ? styles.running : ''
}

function progressText(job: JobRecord): string {
  const completed = job.completed_nodes ?? 0
  const total = job.total_nodes ?? 0
  if (total <= 0) return '—'
  return `${completed}/${total}`
}

function currentNodeSummary(job: JobRecord): JobNodeSummary | undefined {
  const summaries = job.node_summaries ?? []

  // Empty summaries but workflow has nodes: show a pending placeholder
  if (summaries.length === 0 && (job.total_nodes ?? 0) > 0) {
    return {
      node_key: 'pending-start',
      label: '待调度',
      status: 'pending',
      error_message: '',
    }
  }

  // Prefer explicitly active node
  if (job.active_node_key) {
    const found = summaries.find((n) => n.node_key === job.active_node_key)
    if (found) return found
  }

  // Prefer running node
  const running = summaries.find((n) => n.status === 'running')
  if (running) return running

  // Fall back based on overall job status
  if (job.status === 'failed') {
    return (
      summaries.find((n) => n.status === 'failed') ??
      summaries.find((n) => n.status === 'stale') ??
      summaries[summaries.length - 1]
    )
  }
  if (job.status === 'completed') {
    const completed = summaries.filter((n) => n.status === 'completed')
    return completed[completed.length - 1] ?? summaries[summaries.length - 1]
  }

  // queued / pending / default: first pending or first node
  return (
    summaries.find((n) => n.status === 'pending') ??
    summaries.find((n) => n.status === 'stale') ??
    summaries[0]
  )
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

  const currentSummary = currentNodeSummary(job)
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
      <div className={styles.statusEnd}>
        <div className={styles.statusEndRow}>
          {currentSummary && (
            <span
              className={`${styles.activeLabel} ${activeLabelClass(currentSummary.status)}`}
              title={currentSummary.label}
            >
              {currentSummary.label}
            </span>
          )}
          <JobNodeStepper
            nodeSummaries={job.node_summaries ?? []}
            activeNodeKey={currentSummary?.node_key}
            totalNodes={job.total_nodes ?? 0}
          />
          <span className={`${styles.badge} ${statusClass(status)}`}>
            {JOB_STATUS_LABELS[job.status] || job.status}
          </span>
        </div>
        {job.error_summary ? (
          <div className={styles.errorSummary}>{job.error_summary}</div>
        ) : null}
      </div>
    </div>
  )
}
