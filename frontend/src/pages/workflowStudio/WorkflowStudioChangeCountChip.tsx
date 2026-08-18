import { Chip } from '@mui/material'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { countNodeChanges } from './workflowStudioDagChanges'

/** 顶栏未发布变更计数：按 compare node_changes 汇总 added/modified/removed，无变更不渲染。 */
export function WorkflowStudioChangeCountChip({
  summary,
}: {
  summary: ChangeSummaryViewModel | null
}) {
  const counts = countNodeChanges(summary)
  if (!counts) return null
  return (
    <Chip
      size="small"
      color="info"
      label={`未发布变更 ${counts.total}`}
      title={`新增 ${counts.added} · 已改 ${counts.modified} · 已删 ${counts.removed}${summary?.createsRevision ? ' · 将创建新版本' : ''}`}
    />
  )
}
