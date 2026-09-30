import { Chip, CircularProgress } from '@mui/material'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { countNodeChanges } from '../canvas/workflowStudioDagChanges'
import { WorkflowStudioChangeCountChip } from './WorkflowStudioChangeCountChip'
import { WorkflowStudioValidationChip } from './WorkflowStudioValidationChip'
import islandStyles from './StudioCanvasIslands.module.css'

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
 * 更改」在查看 revision 期间持续可见。
 * 窄屏降级自控（codex 轮 4 P1-2）：校验失败/校验中是发布被禁时唯一的
 * 报告入口，窄屏保留紧凑可点击；其余态挂 island secondary（窄屏隐藏）。
 * 岛侧包装用恒透传 .passthrough（不再整组 conditional 一刀切）。 */
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
        className={islandStyles.secondary}
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
        className={islandStyles.secondary}
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
          className={islandStyles.secondary}
          color="warning"
          label="已保留当前草稿"
          title={preservedText ?? undefined}
        />
      )
    }
    return null
  }
  if (
    props.validating ||
    props.validationMessage === '校验通过' ||
    props.validationMessage.startsWith('校验失败')
  ) {
    return (
      <WorkflowStudioValidationChip
        validating={props.validating}
        validationMessage={props.validationMessage}
        onShowChanges={props.onShowChanges}
      />
    )
  }
  if (counts) {
    return (
      <WorkflowStudioChangeCountChip
        summary={props.summary}
        preservedText={preservedText}
        onShowChanges={props.onShowChanges}
        className={islandStyles.secondary}
      />
    )
  }
  return (
    <Chip
      size="small"
      className={islandStyles.secondary}
      color="info"
      label="有未发布变更"
      title={preservedText ?? undefined}
      onClick={props.onShowChanges}
    />
  )
}
