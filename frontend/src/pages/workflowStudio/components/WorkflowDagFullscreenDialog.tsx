import { Fullscreen, Close } from '@mui/icons-material'
import { Dialog, IconButton, Toolbar, Tooltip, Typography } from '@mui/material'
import type { WorkflowDefinitionRecord } from '../../../types'
import { DagGraph } from '../../../components/DagGraph'
import { buildDagEdges, buildDagNodes } from '../workflowStudioDag'
import styles from './WorkflowDagFullscreenDialog.module.css'

type Props = {
  open: boolean
  workflow: WorkflowDefinitionRecord | null
  selectedNode: string | null
  onSelectedNodeChange: (key: string | null) => void
  onClose: () => void
}

export function WorkflowDagFullscreenDialog({
  open,
  workflow,
  selectedNode,
  onSelectedNodeChange,
  onClose,
}: Props) {
  const nodes = buildDagNodes(workflow)
  const edges = buildDagEdges(workflow)

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
        {workflow && (
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

export function WorkflowDagFullscreenButton({
  onClick,
}: {
  onClick: () => void
}) {
  return (
    <Tooltip title="全屏查看 DAG">
      <IconButton
        size="small"
        onClick={onClick}
        aria-label="open fullscreen DAG"
      >
        <Fullscreen />
      </IconButton>
    </Tooltip>
  )
}
