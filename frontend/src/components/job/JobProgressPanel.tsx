import { useState, useCallback, useMemo } from 'react'
import { IconButton } from '@mui/material'
import { DagStepper } from '../dag/DagStepper'
import { MaterialIcon } from '../MaterialIcon'
import { filterRelevantRuns } from '../../lib/jobRuns'
import type { JobNode, NodeRun } from '../../types/jobTypes'
import { JobLogDialog } from './JobLogDialog'
import { JobProgressPanelNode } from './JobProgressPanelNode'
import styles from './JobProgressPanel.module.css'

interface JobProgressPanelProps {
  jobId: string
  nodes: JobNode[]
  runs: NodeRun[]
  onOpenDagDialog?: () => void
  /** 唤起排查 Dock（#795 PR③）：失败节点的「排查」入口经它上行到
   * JobDetailPage 的 Dock 宿主（带节点上下文）。 */
  onOpenDiagnosis?: (target: { nodeKey: string; nodeLabel: string }) => void
}

export function JobProgressPanel({
  jobId,
  nodes,
  runs,
  onOpenDagDialog,
  onOpenDiagnosis,
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

  // Memoized: the 5s running-state poll re-renders this panel with fresh
  // runs/nodes arrays; the relevance filter and latest-run Map should not
  // rebuild on unrelated state (error expansion, dialogs).
  const relevantRuns = useMemo(
    () => filterRelevantRuns(runs, nodes),
    [runs, nodes]
  )
  const runByNodeKey = useMemo(() => {
    const map = new Map<string, NodeRun>()
    for (const run of relevantRuns) {
      const existing = map.get(run.node_key)
      if (!existing || run.id > existing.id) {
        map.set(run.node_key, run)
      }
    }
    return map
  }, [relevantRuns])

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
          {nodes.map((node, idx) => (
            <JobProgressPanelNode
              key={node.node_key}
              jobId={jobId}
              node={node}
              run={runByNodeKey.get(node.node_key)}
              allNodes={nodes}
              isLast={idx === nodes.length - 1}
              isExpanded={expandedErrors.has(node.node_key)}
              onToggleError={toggleError}
              onOpenLog={setLogDialog}
              onOpenDiagnosis={onOpenDiagnosis}
            />
          ))}
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
