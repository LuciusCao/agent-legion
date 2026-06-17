import { useMemo, useState } from 'react'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import { ancestorClosure, validateRunTo } from '../lib/jobDag'
import {
  computeOrderedNodes,
  excludedJobs,
  type WorkflowNodesByKey,
} from '../lib/workflowNodes'
import styles from './JobRunToDialog.module.css'

export type JobRunToDialogProps = {
  open: boolean
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  itemLabel?: string
  onConfirm: (targetKey: string, startKey?: string) => void | Promise<void>
  onClose: () => void
}

export function JobRunToDialog({
  open,
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  itemLabel = '任务',
  onConfirm,
  onClose,
}: JobRunToDialogProps) {
  const orderedNodes = useMemo(
    () => computeOrderedNodes(jobs, workflowDefinition, workflowNodesByKey),
    [jobs, workflowDefinition, workflowNodesByKey]
  )

  const [targetKey, setTargetKey] = useState<string | null>(
    orderedNodes[0]?.key ?? null
  )
  const [startKey, setStartKey] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const effectiveTargetKey =
    targetKey && orderedNodes.some((n) => n.key === targetKey)
      ? targetKey
      : (orderedNodes[0]?.key ?? null)

  // Reset start key if it becomes invalid when target changes
  const closure = useMemo(
    () => ancestorClosure(orderedNodes, effectiveTargetKey ?? ''),
    [orderedNodes, effectiveTargetKey]
  )

  const effectiveStartKey =
    startKey && startKey !== effectiveTargetKey ? startKey : null

  const validation = useMemo(
    () =>
      validateRunTo(
        orderedNodes,
        effectiveTargetKey ?? '',
        effectiveStartKey ?? undefined
      ),
    [orderedNodes, effectiveTargetKey, effectiveStartKey]
  )

  const excluded = effectiveTargetKey
    ? excludedJobs(
        jobs,
        effectiveTargetKey,
        workflowNodesByKey,
        workflowDefinition
      )
    : []

  if (!open) return null

  const handleConfirm = async () => {
    if (!effectiveTargetKey || !validation.valid) return
    setLoading(true)
    try {
      await onConfirm(effectiveTargetKey, effectiveStartKey ?? undefined)
    } finally {
      setLoading(false)
    }
    onClose()
  }

  return (
    <md-dialog
      open
      onClosed={onClose}
      style={
        {
          minWidth: '520px',
          maxWidth: '760px',
          width: 'min(760px, 92vw)',
          '--md-dialog-container-color': '#ffffff',
        } as React.CSSProperties
      }
    >
      <div slot="headline">选择运行到节点</div>
      <div slot="content">
        <div className={styles.content}>
          {orderedNodes.length === 0 ? (
            <p className={styles.empty}>没有可运行到的公共节点</p>
          ) : (
            <>
              <div>
                <div className={styles.sectionLabel}>目标节点</div>
                <div className={styles.nodeGrid}>
                  {orderedNodes.map((node) => (
                    <md-filter-chip
                      key={node.key}
                      data-testid={`target-chip-${node.key}`}
                      label={node.label || node.key}
                      selected={effectiveTargetKey === node.key || undefined}
                      onClick={() => {
                        setTargetKey(node.key)
                        setStartKey(null)
                      }}
                    />
                  ))}
                </div>
              </div>

              {effectiveTargetKey && orderedNodes.length > 1 && (
                <div>
                  <div className={styles.sectionLabel}>起始节点（可选）</div>
                  <div className={styles.nodeGrid}>
                    <md-filter-chip
                      data-testid="start-chip-auto"
                      label="从首个依赖开始"
                      selected={effectiveStartKey === null || undefined}
                      onClick={() => setStartKey(null)}
                    />
                    {orderedNodes
                      .filter((node) => node.key !== effectiveTargetKey)
                      .map((node) => (
                        <md-filter-chip
                          key={node.key}
                          data-testid={`start-chip-${node.key}`}
                          label={node.label || node.key}
                          selected={effectiveStartKey === node.key || undefined}
                          onClick={() => setStartKey(node.key)}
                        />
                      ))}
                  </div>
                </div>
              )}

              {effectiveTargetKey && (
                <div className={styles.closureBox}>
                  <div className={styles.closureTitle}>
                    将运行以下节点及其依赖：
                  </div>
                  <div className={styles.closureChips}>
                    {closure
                      .slice()
                      .sort(
                        (a, b) =>
                          orderedNodes.findIndex((n) => n.key === a) -
                          orderedNodes.findIndex((n) => n.key === b)
                      )
                      .map((key) => (
                        <span key={key} className={styles.closureChip}>
                          {orderedNodes.find((n) => n.key === key)?.label ||
                            key}
                        </span>
                      ))}
                  </div>
                </div>
              )}

              {excluded.length > 0 && effectiveTargetKey && (
                <div className={styles.excludedBox}>
                  <div className={styles.excludedTitle}>
                    以下任务不包含所选节点，将被跳过：
                  </div>
                  <ul className={styles.excludedList}>
                    {excluded.map((job) => (
                      <li key={job.id}>
                        {job.source_id || job.title || job.id}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {!validation.valid && validation.message && (
                <p className={styles.error}>{validation.message}</p>
              )}

              <div className={styles.summary}>
                已选择 {jobs.length} 个{itemLabel}
                {effectiveTargetKey
                  ? `，运行到：${orderedNodes.find((n) => n.key === effectiveTargetKey)?.label || effectiveTargetKey}`
                  : ''}
              </div>
            </>
          )}
        </div>
      </div>
      <div slot="actions">
        <md-text-button
          type="button"
          onClick={onClose}
          disabled={loading || undefined}
        >
          取消
        </md-text-button>
        <md-filled-button
          onClick={handleConfirm}
          disabled={
            !effectiveTargetKey || !validation.valid || loading || undefined
          }
        >
          确认运行到
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
