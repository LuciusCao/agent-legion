import { useState, useCallback } from 'react'
import type { CSSProperties } from 'react'
import { DagStepper } from './DagStepper'
import { durationSeconds } from '../helpers'
import type { JobNodeRecord, NodeRunRecord } from '../types'
import { JOB_STATUS_LABELS } from '../labels'
import styles from './JobProgressPanel.module.css'

const STATUS_ICONS: Record<string, string> = {
  completed: 'check',
  running: 'sync',
  failed: 'error',
  pending: 'schedule',
}

const NODE_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
  pending: styles.statusPending,
}

const BADGE_STATUS_CLASS: Record<string, string> = {
  completed: styles.badgeCompleted,
  running: styles.badgeRunning,
  failed: styles.badgeFailed,
  pending: styles.badgePending,
}

function formatDuration(seconds?: number): string {
  if (seconds === undefined) return '—'
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m${s}s`
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN')
}

interface JobProgressPanelProps {
  nodes: JobNodeRecord[]
  runs: NodeRunRecord[]
  artifacts: string[]
  activeArtifact: string | null
  activeArtifactContent: string
  onArtifactClick: (name: string) => void
}

export function JobProgressPanel({
  nodes,
  runs,
  artifacts,
  activeArtifact,
  activeArtifactContent,
  onArtifactClick,
}: JobProgressPanelProps) {
  const [expandedErrors, setExpandedErrors] = useState<Set<number>>(new Set())
  const [logDialog, setLogDialog] = useState<{
    nodeKey: string
    content: string
  } | null>(null)

  const toggleError = useCallback((runId: number) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev)
      if (next.has(runId)) {
        next.delete(runId)
      } else {
        next.add(runId)
      }
      return next
    })
  }, [])

  const runByNodeKey = new Map<string, NodeRunRecord>()
  for (const run of runs) {
    const existing = runByNodeKey.get(run.node_key)
    if (!existing || run.id > existing.id) {
      runByNodeKey.set(run.node_key, run)
    }
  }

  const dialogStyle = {
    '--md-dialog-container-color': '#ffffff',
    maxWidth: '760px',
    width: '90vw',
  } as CSSProperties

  return (
    <div className={styles.panel}>
      <div className={styles.stepperWrap}>
        <h3 className={styles.panelTitle}>节点进度</h3>
        <DagStepper nodes={nodes} />
      </div>

      <div>
        <h3 className={styles.panelTitle}>阶段明细</h3>
        {nodes.length === 0 && <p className={styles.emptyState}>暂无节点</p>}
        <div className={styles.timeline}>
          {nodes.map((node, idx) => {
            const run = runByNodeKey.get(node.node_key)
            const icon = STATUS_ICONS[node.status] || 'help'
            const statusText = JOB_STATUS_LABELS[node.status] || node.status
            const hasError = !!(run?.error_message || node.error_message)
            const isExpanded = run ? expandedErrors.has(run.id) : false
            const dur = durationSeconds(node.started_at, node.finished_at)

            return (
              <div key={node.node_key} className={styles.timelineItem}>
                <div className={styles.timelineLeft}>
                  <div
                    className={`${styles.timelineNode} ${NODE_STATUS_CLASS[node.status] || ''} ${node.status === 'running' ? styles.spinning : ''}`}
                  >
                    <md-icon>{icon}</md-icon>
                  </div>
                  {idx < nodes.length - 1 && (
                    <div className={styles.timelineLine} />
                  )}
                </div>

                <div className={styles.timelineContent}>
                  <div className={styles.timelineHeader}>
                    <span className={styles.nodeName}>{node.node_key}</span>
                    <span
                      className={`${styles.statusBadge} ${BADGE_STATUS_CLASS[node.status] || ''}`}
                    >
                      {statusText}
                    </span>
                  </div>

                  <div className={styles.timelineMeta}>
                    <span>
                      <md-icon className={styles.metaIcon}>schedule</md-icon>
                      {formatTime(node.started_at)}
                    </span>
                    <span>
                      <md-icon className={styles.metaIcon}>timer</md-icon>
                      {formatDuration(dur)}
                    </span>
                  </div>

                  {run?.log_path && (
                    <button
                      className={styles.logBtn}
                      onClick={() =>
                        setLogDialog({
                          nodeKey: node.node_key,
                          content: run.log_path,
                        })
                      }
                    >
                      <md-icon className={styles.toggleIcon}>
                        description
                      </md-icon>
                      查看日志
                    </button>
                  )}

                  {hasError && (
                    <button
                      className={styles.detailToggle}
                      onClick={() => run && toggleError(run.id)}
                    >
                      <md-icon className={styles.toggleIcon}>error</md-icon>
                      错误详情
                      <md-icon className={styles.toggleIcon}>
                        {isExpanded ? 'expand_less' : 'expand_more'}
                      </md-icon>
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

      {artifacts.length > 0 && (
        <div className={styles.artifactsSection}>
          <h3 className={styles.panelTitle}>产物文件</h3>
          <ul className={styles.artifactList}>
            {artifacts.map((name) => (
              <li key={name}>
                <button
                  type="button"
                  className={`${styles.artifactBtn} ${activeArtifact === name ? styles.artifactBtnActive : ''}`}
                  onClick={() => onArtifactClick(name)}
                >
                  {name}
                </button>
              </li>
            ))}
          </ul>
          {activeArtifact && activeArtifactContent && (
            <div className={styles.artifactPreview}>
              <h4 className={styles.artifactName}>{activeArtifact}</h4>
              <pre className={styles.artifactPre}>{activeArtifactContent}</pre>
            </div>
          )}
        </div>
      )}

      {logDialog && (
        <md-dialog open onClosed={() => setLogDialog(null)} style={dialogStyle}>
          <div slot="headline">日志 — {logDialog.nodeKey}</div>
          <div slot="content" className={styles.dialogContent}>
            <pre className={styles.logPreview}>{logDialog.content}</pre>
          </div>
          <div slot="actions">
            <md-text-button onClick={() => setLogDialog(null)}>
              关闭
            </md-text-button>
          </div>
        </md-dialog>
      )}
    </div>
  )
}
