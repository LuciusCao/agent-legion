import { Close } from '@mui/icons-material'
import { Dialog, IconButton, Toolbar, Tooltip, Typography } from '@mui/material'
import type { WorkflowDefinitionRecord } from '../../../types'
import type { ExecutorDefinition } from '../../../types/executorTypes'
import { DagGraph } from '../../../components/DagGraph'
import { buildDagEdges, buildDagNodes } from '../workflowStudioDag'
import styles from './WorkflowDagFullscreenDialog.module.css'

type Props = {
  open: boolean
  workflow: WorkflowDefinitionRecord | null
  executorCatalog: ExecutorDefinition[]
  selectedNode: string | null
  onSelectedNodeChange: (key: string | null) => void
  onClose: () => void
}

export function WorkflowDagFullscreenDialog({
  open,
  workflow,
  executorCatalog,
  selectedNode,
  onSelectedNodeChange,
  onClose,
}: Props) {
  const nodes = buildDagNodes(workflow, executorCatalog)
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
