import { useNavigate } from 'react-router-dom'
import { MiniDag, type MiniDagNode } from './MiniDag'
import { NodeRunsTable, type NodeRun } from './NodeRunsTable'
import { MaterialIcon } from './MaterialIcon'
import { JOB_STATUS_LABELS } from '../labels'
import type { JobRecord } from '../types'
import styles from './ExpandedJobPanel.module.css'

export interface ExpandedJobPanelProps {
  job: JobRecord
  workspaceId?: string
  onViewDetail: () => void
  onRerun: () => void
  onRunTo: () => void
  onDelete: () => void
}

const DEFAULT_NODES: Array<{ key: string; label: string }> = [
  { key: 'extract', label: '提取' },
  { key: 'generate', label: '生成' },
  { key: 'review', label: '审核' },
  { key: 'assemble', label: '组装' },
  { key: 'package', label: '打包' },
]

function deriveNodeStatus(
  index: number,
  completed: number,
  jobStatus: string
): MiniDagNode['status'] {
  if (index < completed) return 'completed'
  if (index === completed) {
    if (jobStatus === 'failed') return 'failed'
    if (jobStatus === 'running') return 'running'
    return 'pending'
  }
  return 'pending'
}

function buildMiniDagNodes(job: JobRecord): MiniDagNode[] {
  const completed = job.completed_nodes ?? 0
  return DEFAULT_NODES.map((node, index) => ({
    key: node.key,
    label: node.label,
    status: deriveNodeStatus(index, completed, job.status),
  }))
}

function buildNodeRuns(job: JobRecord): NodeRun[] {
  const nodes = buildMiniDagNodes(job)
  return nodes.map((node) => ({
    nodeKey: node.key,
    nodeLabel: node.label,
    status: node.status,
    time: '—',
    duration: '—',
  }))
}

export function ExpandedJobPanel({
  job,
  workspaceId,
  onViewDetail,
  onRerun,
  onRunTo,
  onDelete,
}: ExpandedJobPanelProps) {
  const navigate = useNavigate()
  const dagNodes = buildMiniDagNodes(job)
  const runs = buildNodeRuns(job)
  const statusLabel = JOB_STATUS_LABELS[job.status] || job.status

  return (
    <div className={styles.panel} data-expanded-job={job.id}>
      <div className={styles.header}>
        <div className={styles.titleBlock}>
          <span className={styles.sourceId}>{job.source_id}</span>
          <span className={styles.title}>{job.title || '—'}</span>
        </div>
        <span
          className={`${styles.badge} ${
            styles[job.status as keyof typeof styles] || styles.pending
          }`}
        >
          {statusLabel}
        </span>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>节点工作流</div>
        <MiniDag nodes={dagNodes} />
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>运行记录</div>
        <NodeRunsTable runs={runs} />
      </div>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.actionBtn}
          onClick={() => {
            if (workspaceId) {
              navigate(`/workspaces/${workspaceId}/jobs/${job.id}`)
            }
            onViewDetail()
          }}
        >
          <MaterialIcon className={styles.icon} name="description" />{' '}
          查看完整详情
        </button>
        <button type="button" className={styles.actionBtn} onClick={onRerun}>
          <MaterialIcon className={styles.icon} name="restart_alt" /> 重跑
        </button>
        <button type="button" className={styles.actionBtn} onClick={onRunTo}>
          <MaterialIcon className={styles.icon} name="play_arrow" /> 运行到...
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.danger}`}
          onClick={onDelete}
        >
          <MaterialIcon className={styles.icon} name="delete" /> 删除
        </button>
      </div>
    </div>
  )
}
