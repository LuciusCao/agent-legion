import { Chip } from '@mui/material'
import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import {
  edgeChangeCounts,
  metadataChangeCounts,
  nodeChangeCounts,
} from '../workflowStudioChangeCounts'

type Props = {
  summary: ChangeSummaryViewModel
}

export function WorkflowSummaryChangeCountChips({ summary }: Props) {
  const nodes = nodeChangeCounts(summary)
  const edges = edgeChangeCounts(summary)
  const metadata = metadataChangeCounts(summary)
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
      {metadata > 0 && (
        <Chip
          label={`元数据 ${metadata}`}
          size="small"
          variant="outlined"
        />
      )}
    </>
  )
}
