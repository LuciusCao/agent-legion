import { Chip, CircularProgress } from '@mui/material'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { countNodeChanges } from '../canvas/workflowStudioDagChanges'

type Props = {
  readOnly: boolean
  /** 查看历史 revision 时的版本号（readOnly 时展示）。 */
  version: number | null
  dirty: boolean
  hasPreservedDraft: boolean
  summary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  onShowChanges: () => void
}

const RISK_TEXT = {
  breaking: '风险：高',
  warning: '风险：中',
  info: '风险：低',
} as const

/** 顶栏统一状态 chip：合并 已同步/只读/未发布变更计数/风险/计算中/已保留草稿，
 * 有变更时点击打开变更面板，颜色直接编码风险等级。 */
export function WorkflowStudioStatusChip(props: Props) {
  if (props.compareState === 'loading') {
    return (
      <Chip
        size="small"
        icon={<CircularProgress size={12} />}
        label="计算中…"
      />
    )
  }
  const counts = countNodeChanges(props.summary)
  const preservedText = props.hasPreservedDraft
    ? '已保留当前草稿（基线更新未覆盖你的编辑）'
    : null
  if (props.readOnly) {
    return (
      <Chip
        size="small"
        color={props.hasPreservedDraft ? 'warning' : 'default'}
        label={`只读 v${props.version ?? '-'}`}
        title={preservedText ?? undefined}
      />
    )
  }
  if (counts) {
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
      preservedText,
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
  if (props.dirty) {
    return (
      <Chip
        size="small"
        color="info"
        label="有未发布变更"
        title={preservedText ?? undefined}
        onClick={props.onShowChanges}
      />
    )
  }
  if (props.hasPreservedDraft) {
    return (
      <Chip
        size="small"
        color="warning"
        label="已保留当前草稿"
        title={preservedText ?? undefined}
      />
    )
  }
  // 无变更且非只读：中性「已同步」，保持安静不可点。
  return <Chip size="small" label="已同步" />
}
