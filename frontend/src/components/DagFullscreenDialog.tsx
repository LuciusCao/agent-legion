import { useCallback, useMemo, useState } from 'react'
import {
  DagGraph,
  type DagGraphNode,
  type DagGraphEdge,
  type NodeRunSummary,
} from './DagGraph'
import { JobLogDialog } from './JobLogDialog'
import styles from './DagFullscreenDialog.module.css'

interface DagFullscreenDialogProps {
  open: boolean
  jobId: string
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  runs?: NodeRunSummary[]
  onClose: () => void
}

function runStartedAtMs(run: NodeRunSummary): number {
  const timestamp = new Date(run.started_at).getTime()
  return Number.isNaN(timestamp) ? 0 : timestamp
}

export function DagFullscreenDialog({
  open,
  jobId,
  nodes,
  edges,
  runs,
  onClose,
}: DagFullscreenDialogProps) {
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [logNodeKey, setLogNodeKey] = useState<string | null>(null)

  const handleClose = useCallback(() => {
    setSelectedNode(null)
    setLogNodeKey(null)
    // Clear focus so that any focus captured inside the DAG (e.g. ReactFlow
    // nodes or Material Web buttons) is released before the overlay is removed.
    // This prevents the app-bar buttons from becoming unresponsive after the
    // dialog is closed.
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur()
    }
    onClose()
  }, [onClose])

  const handleBackdropClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) {
        handleClose()
      }
    },
    [handleClose]
  )

  const handleCloseLogDialog = useCallback(() => {
    setLogNodeKey(null)
  }, [])

  const selectedRun = useMemo(
    () =>
      runs
        ?.filter((run) => run.node_key === logNodeKey)
        .sort((a, b) => {
          const startedAtDelta = runStartedAtMs(b) - runStartedAtMs(a)
          return startedAtDelta === 0 ? b.id - a.id : startedAtDelta
        })[0] || null,
    [runs, logNodeKey]
  )

  const selectedNodeLabel = useMemo(() => {
    if (!logNodeKey) return ''
    return nodes.find((node) => node.key === logNodeKey)?.label || logNodeKey
  }, [nodes, logNodeKey])

  if (!open) return null

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      aria-label="DAG 视图"
      onClick={handleBackdropClick}
    >
      <div className={styles.dialog}>
        <div className={styles.header}>
          <span className={styles.title}>DAG 视图</span>
          <div className={styles.actions}>
            <md-icon-button aria-label="关闭" onClick={handleClose}>
              <md-icon>close</md-icon>
            </md-icon-button>
          </div>
        </div>
        <div className={styles.canvas}>
          <DagGraph
            nodes={nodes}
            edges={edges}
            runs={runs}
            onViewLogs={setLogNodeKey}
            selectedNode={selectedNode}
            onSelectedNodeChange={setSelectedNode}
          />
        </div>
      </div>
      {selectedRun && (
        <JobLogDialog
          jobId={jobId}
          runId={selectedRun.id}
          nodeLabel={selectedNodeLabel}
          open
          onClose={handleCloseLogDialog}
        />
      )}
    </div>
  )
}
