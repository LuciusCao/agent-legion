import { useMemo, useState } from 'react'
import type { JobSummary, PipelineDefinitionRecord } from '../types'
import styles from './JobRerunDialog.module.css'

export type PipelineNodesByKey = Record<string, PipelineDefinitionRecord>

export type JobRerunDialogProps = {
  open: boolean
  jobs: JobSummary[]
  pipelineDefinition?: PipelineDefinitionRecord | null
  pipelineNodesByKey?: PipelineNodesByKey | null
  itemLabel?: string
  onConfirm: (nodeKey: string) => void | Promise<void>
  onClose: () => void
}

function nodesForJob(
  job: JobSummary,
  pipelineNodesByKey?: PipelineNodesByKey | null,
  pipelineDefinition?: PipelineDefinitionRecord | null
) {
  if (pipelineNodesByKey && job.pipeline_key in pipelineNodesByKey) {
    return pipelineNodesByKey[job.pipeline_key].nodes
  }
  if (pipelineDefinition && job.pipeline_key === pipelineDefinition.key) {
    return pipelineDefinition.nodes
  }
  return null
}

function computeOrderedNodes(
  jobs: JobSummary[],
  pipelineDefinition: PipelineDefinitionRecord | null | undefined,
  pipelineNodesByKey: PipelineNodesByKey | null | undefined
) {
  if (jobs.length === 0) return []

  const jobsWithNodes = jobs
    .map((job) => ({
      job,
      nodes: nodesForJob(job, pipelineNodesByKey, pipelineDefinition),
    }))
    .filter(
      (
        entry
      ): entry is { job: JobSummary; nodes: NonNullable<typeof entry.nodes> } =>
        !!entry.nodes
    )

  if (jobsWithNodes.length === 0) return []

  const firstNodes = jobsWithNodes[0].nodes
  const knownPipelineKeys = new Set(
    jobsWithNodes.map((entry) => entry.job.pipeline_key)
  )
  const hasMultipleKnownPipelines = knownPipelineKeys.size > 1

  if (!hasMultipleKnownPipelines) {
    return firstNodes
  }

  const keySets = jobsWithNodes.map(
    (entry) => new Set(entry.nodes.map((n) => n.key))
  )
  const commonKeys = new Set(
    [...keySets[0]].filter((key) => keySets.every((set) => set.has(key)))
  )

  return firstNodes.filter((n) => commonKeys.has(n.key))
}

function excludedJobs(
  jobs: JobSummary[],
  nodeKey: string,
  pipelineNodesByKey: PipelineNodesByKey | null | undefined,
  pipelineDefinition: PipelineDefinitionRecord | null | undefined
): JobSummary[] {
  return jobs.filter((job) => {
    const nodes = nodesForJob(job, pipelineNodesByKey, pipelineDefinition)
    if (!nodes) return true
    return !nodes.some((n) => n.key === nodeKey)
  })
}

export function JobRerunDialog({
  open,
  jobs,
  pipelineDefinition,
  pipelineNodesByKey,
  itemLabel = '任务',
  onConfirm,
  onClose,
}: JobRerunDialogProps) {
  const orderedNodes = useMemo(
    () => computeOrderedNodes(jobs, pipelineDefinition, pipelineNodesByKey),
    [jobs, pipelineDefinition, pipelineNodesByKey]
  )
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(
    orderedNodes[0]?.key ?? null
  )

  // Keep selected key valid when nodes change
  const effectiveNodeKey =
    selectedNodeKey && orderedNodes.some((n) => n.key === selectedNodeKey)
      ? selectedNodeKey
      : (orderedNodes[0]?.key ?? null)

  const [loading, setLoading] = useState(false)
  const excluded = effectiveNodeKey
    ? excludedJobs(
        jobs,
        effectiveNodeKey,
        pipelineNodesByKey,
        pipelineDefinition
      )
    : []

  if (!open) return null

  const handleConfirm = async () => {
    if (!effectiveNodeKey) return
    setLoading(true)
    try {
      await onConfirm(effectiveNodeKey)
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
      <div slot="headline">选择重跑节点</div>
      <div slot="content">
        <div className={styles.content}>
          {orderedNodes.length === 0 ? (
            <p className={styles.empty}>没有可重跑的公共节点</p>
          ) : (
            <div className={styles.nodeGrid}>
              {orderedNodes.map((node) => (
                <md-filter-chip
                  key={node.key}
                  label={node.label || node.key}
                  selected={effectiveNodeKey === node.key || undefined}
                  onClick={() => setSelectedNodeKey(node.key)}
                />
              ))}
            </div>
          )}

          {excluded.length > 0 && effectiveNodeKey && (
            <div className={styles.excludedBox}>
              <div className={styles.excludedTitle}>
                以下任务不包含所选节点，将被跳过：
              </div>
              <ul className={styles.excludedList}>
                {excluded.map((job) => (
                  <li key={job.id}>{job.source_id || job.title || job.id}</li>
                ))}
              </ul>
            </div>
          )}

          <div className={styles.summary}>
            已选择 {jobs.length} 个{itemLabel}
            {effectiveNodeKey
              ? `，重跑节点：${orderedNodes.find((n) => n.key === effectiveNodeKey)?.label || effectiveNodeKey}`
              : ''}
          </div>
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
          disabled={!effectiveNodeKey || loading || undefined}
        >
          确认重跑
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
