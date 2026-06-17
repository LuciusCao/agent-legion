import { useCallback, useState } from 'react'
import {
  DagGraph,
  type DagGraphNode,
  type DagGraphEdge,
  type NodeRunSummary,
} from './DagGraph'
import styles from './DagFullscreenDialog.module.css'

interface DagFullscreenDialogProps {
  open: boolean
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  runs?: NodeRunSummary[]
  onClose: () => void
}

export function DagFullscreenDialog({
  open,
  nodes,
  edges,
  runs,
  onClose,
}: DagFullscreenDialogProps) {
  const [selectedNode, setSelectedNode] = useState<string | null>(null)

  const handleClose = useCallback(() => {
    setSelectedNode(null)
    onClose()
  }, [onClose])

  const handleViewLogs = useCallback((nodeKey: string) => {
    console.log('view logs', nodeKey)
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
            onViewLogs={handleViewLogs}
            selectedNode={selectedNode}
            onSelectedNodeChange={setSelectedNode}
          />
        </div>
      </div>
    </div>
  )
}
