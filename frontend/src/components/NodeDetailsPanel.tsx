import styles from './NodeDetailsPanel.module.css'
import type { DagNodeData } from './DagNode'

interface LatestRun {
  id: number
  status: string
  started_at: string
  exit_code: number | null
  error_message?: string
}

interface NodeDetailsPanelProps {
  nodeKey: string
  data: DagNodeData
  latestRun: LatestRun | null
  onViewLogs: (nodeKey: string) => void
}

const STATUS_ICON: Record<DagNodeData['status'], string> = {
  completed: 'check_circle',
  running: 'hourglass_empty',
  failed: 'error',
  stale: 'warning',
  pending: 'radio_button_unchecked',
}

const STATUS_LABEL: Record<DagNodeData['status'], string> = {
  completed: '已完成',
  running: '运行中',
  failed: '失败',
  stale: '过期',
  pending: '等待中',
}

export function NodeDetailsPanel({
  nodeKey,
  data,
  latestRun,
  onViewLogs,
}: NodeDetailsPanelProps) {
  const durationText =
    data.status === 'running'
      ? `运行中 ${data.duration ?? 0}s`
      : typeof data.duration === 'number'
        ? `耗时 ${data.duration}s`
        : '—'

  return (
    <div className={styles.panel}>
      <h3 className={styles.title}>{data.label}</h3>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>状态</div>
        <div className={styles.statusRow}>
          <span className={styles.icon}>{STATUS_ICON[data.status]}</span>
          <span>{STATUS_LABEL[data.status]}</span>
          <span className={styles.muted}>{durationText}</span>
        </div>
      </div>

      {data.executorKind && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Executor</div>
          <span className={styles.tag}>{data.executorKind}</span>
        </div>
      )}

      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          输入产物（{data.inputs.length}）
        </div>
        {data.inputs.length === 0 ? (
          <span className={styles.muted}>—</span>
        ) : (
          <ul className={styles.list}>
            {data.inputs.map((input) => (
              <li key={input}>{input}</li>
            ))}
          </ul>
        )}
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          输出产物（{data.outputs.length}）
        </div>
        {data.outputs.length === 0 ? (
          <span className={styles.muted}>—</span>
        ) : (
          <ul className={styles.list}>
            {data.outputs.map((output) => (
              <li key={output}>{output}</li>
            ))}
          </ul>
        )}
      </div>

      {latestRun && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>最近运行</div>
          <div className={styles.runCard}>
            <div>
              Run #{latestRun.id} · {latestRun.status}
            </div>
            <div className={styles.muted}>开始：{latestRun.started_at}</div>
            {latestRun.exit_code !== null && (
              <div>退出码：{latestRun.exit_code}</div>
            )}
            {latestRun.error_message && (
              <div className={styles.error}>{latestRun.error_message}</div>
            )}
          </div>
        </div>
      )}

      <button className={styles.logButton} onClick={() => onViewLogs(nodeKey)}>
        查看日志
      </button>
    </div>
  )
}
