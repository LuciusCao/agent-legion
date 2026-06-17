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

  const selectedRun = useMemo(
    () => runs?.find((run) => run.node_key === logNodeKey) || null,
    [runs, logNodeKey]
  )

  const selectedNodeLabel = useMemo(() => {
    if (!logNodeKey) return ''
    return nodes.find((node) => node.key === logNodeKey)?.label || logNodeKey
  }, [nodes, logNodeKey])

  const handleClose = useCallback(() => {
    setSelectedNode(null)
    setLogNodeKey(null)
    onClose()
  }, [onClose])

  const handleCloseLogDialog = useCallback(() => {
    setLogNodeKey(null)
  }, [])

  if (!open) return null

  return (
    <div className={styles.overlay} onClick={handleClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
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
