import { Chip, CircularProgress } from '@mui/material'
import islandStyles from './StudioCanvasIslands.module.css'

type Props = {
  validating: boolean
  validationMessage: string
  onShowChanges: () => void
}

/** 校验态 chip 三态（从 WorkflowStudioStatusChip 拆出保体积预算）：
 * 校验中… / ✓ 校验通过 / ✗ 校验失败。窄屏降级自控（codex 轮 4 P1-2）：
 * 校验中与校验失败是发布被禁时唯一的报告入口，窄屏保留紧凑可点击
 * （不挂 secondary）；通过态窄屏隐藏（挂 island secondary）。 */
export function WorkflowStudioValidationChip(props: Props) {
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
  return (
    <Chip
      size="small"
      className={islandStyles.secondary}
      color="success"
      label="✓ 校验通过"
      title="自动校验通过——点击查看变更与校验报告"
      onClick={props.onShowChanges}
    />
  )
}
