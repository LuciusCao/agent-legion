import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import {
  edgeChangeCounts,
  metadataChangeCounts,
  nodeChangeCounts,
} from '../workflowStudioChangeCounts'

export function nodeChangeLabel(summary: ChangeSummaryViewModel): string {
  const { added, removed, modified } = nodeChangeCounts(summary)
  return `节点 +${added} / -${removed} / 改 ${modified}`
}

export function edgeChangeLabel(summary: ChangeSummaryViewModel): string {
  const { added, removed, changed } = edgeChangeCounts(summary)
  return `边 +${added} / -${removed} / 改 ${changed}`
}

export function metadataChangeLabel(summary: ChangeSummaryViewModel): string {
  const count = metadataChangeCounts(summary)
  return count > 0 ? `元数据 ${count}` : ''
}
