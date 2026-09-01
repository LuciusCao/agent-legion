import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { MaterialIcon } from '../MaterialIcon'
import { durationSeconds, formatDuration } from '../../lib/formatters'
import type { JobNode, NodeRun } from '../../types/jobTypes'
import { JOB_STATUS_LABELS } from '../../labels'
import { JobDiagnosisDialog } from '../../features/jobDiagnosis/JobDiagnosisDialog'
import {
  computeWaitTime,
  EXECUTOR_KIND_ICONS,
  EXECUTOR_KIND_LABELS,
} from '../jobProgressHelpers'
import { TokenUsageRunDetail } from '../tokenUsage/TokenUsageRunDetail'
import styles from './JobProgressPanel.module.css'

const STATUS_ICONS: Record<string, string> = {
  completed: 'check',
  running: 'sync',
  failed: 'error',
  stale: 'schedule',
  pending: 'schedule',
  awaiting_approval: 'pending_actions',
}

const NODE_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
  stale: styles.statusPending,
  pending: styles.statusPending,
  awaiting_approval: styles.statusAwaitingApproval,
}

const BADGE_STATUS_CLASS: Record<string, string> = {
  completed: styles.badgeCompleted,
  running: styles.badgeRunning,
  failed: styles.badgeFailed,
  stale: styles.badgePending,
  pending: styles.badgePending,
  awaiting_approval: styles.badgeAwaitingApproval,
}

interface JobProgressPanelNodeProps {
  jobId: string
  node: JobNode
  run: NodeRun | undefined
  allNodes: JobNode[]
  isLast: boolean
  isExpanded: boolean
  onToggleError: (nodeKey: string) => void
  onOpenLog: (target: { nodeLabel: string; runId: number }) => void
}

/** 时间线上的单个节点条目（从 JobProgressPanel 拆出，文件预算）。排查入口
 * （#329）挂在这里：仅失败节点展示，弹窗状态全挂本组件内部；workspaceId
 * 取自路由参数（本组件只挂在 workspace 路由下，无路由上下文时 useParams
 * 返回空对象，按钮不渲染）。 */
export function JobProgressPanelNode({
  jobId,
  node,
  run,
  allNodes,
  isLast,
  isExpanded,
  onToggleError,
  onOpenLog,
}: JobProgressPanelNodeProps) {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [diagnosisOpen, setDiagnosisOpen] = useState(false)

  const icon = STATUS_ICONS[node.status] || 'help'
  const statusText = JOB_STATUS_LABELS[node.status] || node.status
  const hasError = !!(run?.error_message || node.error_message)
  const durSeconds = durationSeconds(node.started_at, node.finished_at)
  const durLabel =
    durSeconds === undefined
      ? '—'
      : durSeconds === 0
        ? '0秒'
        : formatDuration(durSeconds * 1000)
  const waitLabel =
    node.status === 'pending'
      ? '等待中'
      : (computeWaitTime(node, allNodes) ?? '—')
  const executorKind = node.executor_kind
  const executorLabel = executorKind
    ? (EXECUTOR_KIND_LABELS[executorKind] ?? executorKind)
    : undefined
  const executorIcon = executorKind
    ? (EXECUTOR_KIND_ICONS[executorKind] ?? 'computer')
    : undefined

  return (
    <div className={styles.timelineItem}>
      <div className={styles.timelineLeft}>
        <div
          className={`${styles.timelineNode} ${NODE_STATUS_CLASS[node.status] || ''} ${node.status === 'running' ? styles.spinning : ''}`}
        >
          <MaterialIcon name={icon} sx={{ fontSize: 14 }} />
        </div>
        {!isLast && <div className={styles.timelineLine} />}
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
            {durLabel}
          </span>
          {executorLabel && executorIcon && (
            <span
              className={styles.executorBadge}
              title={`执行器: ${executorLabel}`}
            >
              <MaterialIcon
                name={executorIcon}
                className={styles.metaIcon}
                sx={{ fontSize: 14 }}
              />
              {executorLabel}
            </span>
          )}
        </div>

        {run?.log_path && (
          <button
            className={styles.logBtn}
            onClick={() => onOpenLog({ nodeLabel: node.label, runId: run.id })}
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
            onClick={() => onToggleError(node.node_key)}
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

        {node.status === 'failed' && workspaceId && (
          <button
            className={styles.logBtn}
            onClick={() => setDiagnosisOpen(true)}
          >
            <MaterialIcon
              name="smart_toy"
              className={styles.toggleIcon}
              sx={{ fontSize: 14 }}
            />
            排查
          </button>
        )}

        {isExpanded && hasError && (
          <div className={styles.errorDetail}>
            {run?.error_message || node.error_message}
          </div>
        )}

        {run && (
          <div className={styles.tokenUsageRow}>
            <TokenUsageRunDetail jobId={jobId} run={run} />
          </div>
        )}
      </div>
      {diagnosisOpen && workspaceId && (
        <JobDiagnosisDialog
          open
          target={{
            workspaceId,
            jobId,
            nodeKey: node.node_key,
            nodeLabel: node.label,
          }}
          onClose={() => setDiagnosisOpen(false)}
        />
      )}
    </div>
  )
}
