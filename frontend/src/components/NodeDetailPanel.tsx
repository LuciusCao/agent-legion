import { JOB_STATUS_LABELS } from '../labels'
import styles from './NodeDetailPanel.module.css'

type NodeStatus = 'pending' | 'running' | 'completed' | 'failed'

const STATUS_CLASS: Record<NodeStatus, string> = {
  completed: styles.completed,
  running: styles.running,
  failed: styles.failed,
  pending: styles.pending,
}

export interface NodeDetailPanelProps {
  node: {
    key: string
    label: string
    status: NodeStatus
    startedAt?: string
    endedAt?: string
    duration?: number
    agentId?: string
  } | null
  onViewLogs: () => void
  onRerunNode: () => void
}

function formatTime(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN')
}

export function NodeDetailPanel({
  node,
  onViewLogs,
  onRerunNode,
}: NodeDetailPanelProps) {
  if (!node) {
    return (
      <div className={styles.panel}>
        <div className={styles.empty}>选择一个节点查看详情</div>
      </div>
    )
  }

  const statusLabel = JOB_STATUS_LABELS[node.status] || node.status

  return (
    <div className={styles.panel} data-testid="node-detail-panel">
      <div className={styles.header}>
        <span className={styles.title}>{node.label}</span>
        <span
          className={`${styles.badge} ${STATUS_CLASS[node.status] || styles.unknown}`}
        >
          {statusLabel}
        </span>
      </div>

      <dl className={styles.rows}>
        <div className={styles.row}>
          <dt>状态</dt>
          <dd>{statusLabel}</dd>
        </div>
        <div className={styles.row}>
          <dt>开始时间</dt>
          <dd>{formatTime(node.startedAt)}</dd>
        </div>
        <div className={styles.row}>
          <dt>结束时间</dt>
          <dd>{formatTime(node.endedAt)}</dd>
        </div>
        <div className={styles.row}>
          <dt>耗时</dt>
          <dd>
            {typeof node.duration === 'number' ? `${node.duration}秒` : '—'}
          </dd>
        </div>
        <div className={styles.row}>
          <dt>智能体</dt>
          <dd>{node.agentId || '—'}</dd>
        </div>
      </dl>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.btn}
          onClick={onViewLogs}
          data-testid="view-logs-btn"
        >
          查看日志
        </button>
        <button
          type="button"
          className={styles.btn}
          onClick={onRerunNode}
          data-testid="rerun-node-btn"
        >
          重跑此节点
        </button>
      </div>
    </div>
  )
}
