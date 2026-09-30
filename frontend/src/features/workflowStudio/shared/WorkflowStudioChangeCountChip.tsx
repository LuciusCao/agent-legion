import { Chip } from '@mui/material'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { countNodeChanges } from '../canvas/workflowStudioDagChanges'

const RISK_TEXT = {
  breaking: '风险：高',
  warning: '风险：中',
  info: '风险：低',
} as const

type Props = {
  summary: ChangeSummaryViewModel | null
  /** 已保留草稿提示（并入 title）。 */
  preservedText: string | null
  onShowChanges: () => void
}

/** 「未发布变更 N」chip（从 WorkflowStudioStatusChip 拆出保体积预算）：
 * 颜色直接编码风险等级，title 带变更明细；点击打开变更面板。 */
export function WorkflowStudioChangeCountChip(props: Props) {
  const counts = countNodeChanges(props.summary)
  if (!counts) return null
  const risk = props.summary?.riskLevel
  const color =
    risk === 'breaking' ? 'error' : risk === 'warning' ? 'warning' : 'info'
  const riskText =
    risk === 'breaking' || risk === 'warning' || risk === 'info'
      ? RISK_TEXT[risk]
      : null
  const title = [
    riskText,
    `新增 ${counts.added} · 已改 ${counts.modified} · 已删 ${counts.removed}`,
    props.summary?.createsRevision ? '将创建新版本' : null,
    props.preservedText,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Chip
      size="small"
      color={color}
      label={`未发布变更 ${counts.total}`}
      title={title}
      onClick={props.onShowChanges}
    />
  )
}
