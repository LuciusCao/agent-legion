import { Chip } from '@mui/material'
import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import {
  edgeChangeCounts,
  nodeChangeCounts,
} from '../workflowStudioChangeCounts'

type Props = {
  summary: ChangeSummaryViewModel
}

export function WorkflowSummaryChangeCountChips({ summary }: Props) {
  const nodes = nodeChangeCounts(summary)
  const edges = edgeChangeCounts(summary)
  return (
    <>
      <Chip
        label={`节点 +${nodes.added} / -${nodes.removed} / 改 ${nodes.modified}`}
        size="small"
        variant="outlined"
      />
      <Chip
        label={`边 +${edges.added} / -${edges.removed} / 改 ${edges.changed}`}
        size="small"
        variant="outlined"
      />
    </>
  )
}
