import { Box, Chip, Typography } from '@mui/material'
import type { DagNode } from '../../lib/jobDag'
import styles from '../JobRerunDialog/JobRerunDialog.module.css'

export type JobAllMatchingNodeRowProps = {
  nodes: DagNode[]
  selectedNodeKey: string | null
  onSelectNode: (key: string) => void
}

/** From-node rerun chips for the allMatching dialog (server resolves jobs). */
export function JobAllMatchingNodeRow({
  nodes,
  selectedNodeKey,
  onSelectNode,
}: JobAllMatchingNodeRowProps) {
  if (nodes.length === 0) return null
  return (
    <>
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        从节点重跑
      </Typography>
      <Box
        className={styles.nodeGrid}
        sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2 }}
      >
        {nodes.map((node) => (
          <Chip
            key={node.key}
            data-testid={`rerun-chip-${node.key}`}
            label={node.label || node.key}
            variant={selectedNodeKey === node.key ? 'filled' : 'outlined'}
            onClick={() => onSelectNode(node.key)}
          />
        ))}
      </Box>
    </>
  )
}
