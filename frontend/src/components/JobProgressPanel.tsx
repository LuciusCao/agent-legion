import { useState, useCallback } from 'react'
import { IconButton } from '@mui/material'
import { DagStepper } from './DagStepper'
import { MaterialIcon } from './MaterialIcon'
import { durationSeconds, filterRelevantRuns } from '../helpers'
import type { JobNodeRecord, NodeRunRecord } from '../types'
import { JOB_STATUS_LABELS } from '../labels'
import { JobLogDialog } from './JobLogDialog'
import styles from './JobProgressPanel.module.css'

const STATUS_ICONS: Record<string, string> = {
  completed: 'check',
  running: 'sync',
  failed: 'error',
  stale: 'schedule',
  pending: 'schedule',
}

const NODE_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
  stale: styles.statusPending,
  pending: styles.statusPending,
}

const BADGE_STATUS_CLASS: Record<string, string> = {
  completed: styles.badgeCompleted,
  running: styles.badgeRunning,
  failed: styles.badgeFailed,
  stale: styles.badgePending,
  pending: styles.badgePending,
}

function formatDuration(seconds?: number): string {
  if (seconds === undefined) return '—'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}m${s}s`
  }
  const h = Math.floor(seconds / 3600)
  const remainder = seconds % 3600
  const m = Math.floor(remainder / 60)
  const s = remainder % 60
  return `${h}h${m}m${s}s`
}

function computeWaitTime(
  node: JobNodeRecord,
  nodes: JobNodeRecord[]
): string | undefined {
  if (!node.started_at) return undefined
  const started = new Date(node.started_at).getTime()
  if (Number.isNaN(started)) return undefined

  let readySince: number | undefined = node.created_at
    ? new Date(node.created_at).getTime()
    : undefined
  if (readySince !== undefined && Number.isNaN(readySince)) {
    readySince = undefined
  }

  for (const depKey of node.after ?? []) {
    const depNode = nodes.find((n) => n.node_key === depKey)
    if (!depNode?.finished_at) continue
    const depFinished = new Date(depNode.finished_at).getTime()
    if (Number.isNaN(depFinished)) continue
    readySince =
      readySince === undefined ? depFinished : Math.max(readySince, depFinished)
  }

  if (readySince === undefined) return undefined
  const seconds = Math.max(0, Math.floor((started - readySince) / 1000))
  return formatDuration(seconds)
}

interface JobProgressPanelProps {
  jobId: string
  nodes: JobNodeRecord[]
  runs: NodeRunRecord[]
  onOpenDagDialog?: () => void
}

export function JobProgressPanel({
  jobId,
  nodes,
  runs,
  onOpenDagDialog,
}: JobProgressPanelProps) {
  const [expandedErrors, setExpandedErrors] = useState<Set<string>>(new Set())
  const [logDialog, setLogDialog] = useState<{
    nodeLabel: string
    runId: number
  } | null>(null)

  const toggleError = useCallback((nodeKey: string) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev)
      if (next.has(nodeKey)) {
        next.delete(nodeKey)
      } else {
        next.add(nodeKey)
      }
      return next
    })
  }, [])

  const relevantRuns = filterRelevantRuns(runs, nodes)
  const runByNodeKey = new Map<string, NodeRunRecord>()
  for (const run of relevantRuns) {
    const existing = runByNodeKey.get(run.node_key)
    if (!existing || run.id > existing.id) {
      runByNodeKey.set(run.node_key, run)
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.stepperWrap}>
        <div className={styles.stepperHeader}>
          <DagStepper nodes={nodes} />
          {onOpenDagDialog && (
            <IconButton aria-label="查看 DAG" onClick={onOpenDagDialog}>
              <MaterialIcon name="account_tree" />
            </IconButton>
          )}
        </div>
      </div>

      <div>
        {nodes.length === 0 && <p className={styles.emptyState}>暂无节点</p>}
        <div className={styles.timeline}>
          {nodes.map((node, idx) => {
            const run = runByNodeKey.get(node.node_key)
            const icon = STATUS_ICONS[node.status] || 'help'
            const statusText = JOB_STATUS_LABELS[node.status] || node.status
            const hasError = !!(run?.error_message || node.error_message)
            const isExpanded = expandedErrors.has(node.node_key)
            const dur = durationSeconds(node.started_at, node.finished_at)
            const waitLabel =
              node.status === 'pending'
                ? '等待中'
                : (computeWaitTime(node, nodes) ?? '—')

            return (
              <div key={node.node_key} className={styles.timelineItem}>
                <div className={styles.timelineLeft}>
                  <div
                    className={`${styles.timelineNode} ${NODE_STATUS_CLASS[node.status] || ''} ${node.status === 'running' ? styles.spinning : ''}`}
                  >
                    <MaterialIcon name={icon} sx={{ fontSize: 14 }} />
                  </div>
                  {idx < nodes.length - 1 && (
                    <div className={styles.timelineLine} />
                  )}
                </div>

                <div className={styles.timelineContent}>
                  <div className={styles.timelineHeader}>
                    <span
                      className={`${styles.nodeName} ${node.status === 'running' ? styles.nodeNameRunning : ''}`}
                    >
                      {node.label}
                    </span>
                    <span
                      className={`${styles.statusBadge} ${BADGE_STATUS_CLASS[node.status] || ''}`}
                    >
                      {statusText}
                    </span>
                  </div>

                  <div className={styles.timelineTimes}>
                    <span>
                      <MaterialIcon
                        name="schedule"
                        className={styles.metaIcon}
                        sx={{ fontSize: 14 }}
                      />
                      {waitLabel}
                    </span>
                    <span>
                      <MaterialIcon
                        name="timer"
                        className={styles.metaIcon}
                        sx={{ fontSize: 14 }}
                      />
                      {formatDuration(dur)}
                    </span>
                  </div>

                  {run?.log_path && (
                    <button
                      className={styles.logBtn}
                      onClick={() =>
                        setLogDialog({
                          nodeLabel: node.label,
                          runId: run.id,
                        })
                      }
                    >
                      <MaterialIcon
                        name="description"
                        className={styles.toggleIcon}
                        sx={{ fontSize: 14 }}
                      />
                      查看日志
                    </button>
                  )}

                  {hasError && (
                    <button
                      className={styles.detailToggle}
                      onClick={() => toggleError(node.node_key)}
                    >
                      <MaterialIcon
                        name="error"
                        className={styles.toggleIcon}
                        sx={{ fontSize: 14 }}
                      />
                      错误详情
                      <MaterialIcon
                        name={isExpanded ? 'expand_less' : 'expand_more'}
                        className={styles.toggleIcon}
                        sx={{ fontSize: 14 }}
                      />
                    </button>
                  )}

                  {isExpanded && hasError && (
                    <div className={styles.errorDetail}>
                      {run?.error_message || node.error_message}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {logDialog && (
        <JobLogDialog
          jobId={jobId}
          runId={logDialog.runId}
          nodeLabel={logDialog.nodeLabel}
          open
          onClose={() => setLogDialog(null)}
        />
      )}
    </div>
  )
}
