import { Close } from '@mui/icons-material'
import { Dialog, IconButton, Toolbar, Tooltip, Typography } from '@mui/material'
import { DagGraph } from '../../../components/dag/DagGraph'
import type {
  DagGraphEdge,
  DagGraphNode,
} from '../../../components/dag/DagGraph'
import styles from './WorkflowDagFullscreenDialog.module.css'

type Props = {
  open: boolean
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  selectedNode: string | null
  onSelectedNodeChange: (key: string | null) => void
  onClose: () => void
}

export function WorkflowDagFullscreenDialog({
  open,
  nodes,
  edges,
  selectedNode,
  onSelectedNodeChange,
  onClose,
}: Props) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullScreen
      aria-labelledby="workflow-dag-focus-title"
    >
      <Toolbar className={styles.toolbar}>
        <Typography
          id="workflow-dag-focus-title"
          variant="h6"
          component="div"
          className={styles.title}
        >
          Workflow DAG focus mode
        </Typography>
        <Tooltip title="关闭">
          <IconButton
            edge="end"
            onClick={onClose}
            aria-label="close fullscreen DAG"
          >
            <Close />
          </IconButton>
        </Tooltip>
      </Toolbar>
      <div className={styles.canvas}>
        {nodes.length > 0 && (
          <DagGraph
            nodes={nodes}
            edges={edges}
            selectedNode={selectedNode}
            onSelectedNodeChange={onSelectedNodeChange}
            hideNodeDetails
          />
        )}
      </div>
    </Dialog>
  )
}
