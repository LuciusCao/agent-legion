import { Chip, CircularProgress } from '@mui/material'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { countNodeChanges } from '../canvas/workflowStudioDagChanges'
import { WorkflowStudioChangeCountChip } from './WorkflowStudioChangeCountChip'

type Props = {
  readOnly: boolean
  /** 查看历史 revision 时的版本号（readOnly 时展示）。 */
  version: number | null
  dirty: boolean
  hasPreservedDraft: boolean
  summary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  /** #804 定案：自动校验状态（草稿保存成功后静默校验的结果驱动）。 */
  validating: boolean
  validationMessage: string
  onShowChanges: () => void
}

/** 左岛统一状态 chip（CI 风格）：草稿有未发布变更时经 未发布变更 →
 * 校验中… → ✓ 校验通过（绿）/ ✗ 校验失败（红，点击开校验报告抽屉）；
 * 无变更（干净态）不渲染——「已同步」常态不占位（#804 定案）。草稿再
 * 编辑后旧校验结果由 useValidationFeedback 作废，chip 回「未发布变更」。
 * 只读（查看历史 revision）优先于「计算中…」：compare 因草稿未发布变更
 * 在后台运行时版本标识不闪断，且计数并入只读 chip，让「草稿有未发布
 * 更改」在查看 revision 期间持续可见。 */
export function WorkflowStudioStatusChip(props: Props) {
  const counts = countNodeChanges(props.summary)
  const preservedText = props.hasPreservedDraft
    ? '已保留当前草稿（基线更新未覆盖你的编辑）'
    : null
  if (props.readOnly) {
    const draftChanges = counts ? ` · 草稿未发布变更 ${counts.total}` : ''
    return (
      <Chip
        size="small"
        color={props.hasPreservedDraft || counts ? 'warning' : 'default'}
        label={`只读 v${props.version ?? '-'}${draftChanges}`}
        title={preservedText ?? undefined}
      />
    )
  }
  if (props.compareState === 'loading') {
    return (
      <Chip
        size="small"
        icon={<CircularProgress size={12} />}
        label="计算中…"
      />
    )
  }
  const hasChanges = Boolean(counts) || props.dirty
  if (!hasChanges) {
    // 干净态不显示 chip；保留草稿警示是例外（基线更新没覆盖本页编辑）。
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
    return null
  }
  if (props.validating) {
    return (
      <Chip
        size="small"
        icon={<CircularProgress size={12} />}
        label="校验中…"
        title="草稿已保存，自动校验进行中"
      />
    )
  }
  if (props.validationMessage.startsWith('校验失败')) {
    return (
      <Chip
        size="small"
        color="error"
        label="✗ 校验失败"
        title={`${props.validationMessage}——点击查看校验报告`}
        onClick={props.onShowChanges}
      />
    )
  }
  if (props.validationMessage === '校验通过') {
    return (
      <Chip
        size="small"
        color="success"
        label="✓ 校验通过"
        title="自动校验通过——点击查看变更与校验报告"
        onClick={props.onShowChanges}
      />
    )
  }
  if (counts) {
    return (
      <WorkflowStudioChangeCountChip
        summary={props.summary}
        preservedText={preservedText}
        onShowChanges={props.onShowChanges}
      />
    )
  }
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
