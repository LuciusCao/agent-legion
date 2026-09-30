import { Button } from '@mui/material'

type Props = {
  idle: boolean
  /** 轮 4 P2-E：「设为草稿」会用历史版本覆盖当前草稿——草稿有未发布
   * 变更时必须确认（调用方按 dirty/compare 计数判定）。 */
  confirmAdoptDraft?: boolean
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

/** 只读态（查看历史 revision）动作组（从 WorkflowStudioCommandBarActions
 * 拆出保体积预算）：「返回」用 text 变体——窄屏动作组的 outlined 隐藏
 * 规则误伤不到这个非破坏出口（轮 4 P2-E）；「设为草稿」保持 contained
 * 主按钮，草稿有变更时 window.confirm 确认覆盖。 */
export function WorkflowStudioReadOnlyActions(props: Props) {
  const textBtn = (
    label: string,
    variant: 'text' | 'contained',
    onClick: () => void
  ) => (
    <Button
      size="small"
      variant={variant}
      disabled={!props.idle}
      onClick={onClick}
    >
      {label}
    </Button>
  )
  return (
    <>
      {textBtn('返回', 'text', props.backToDraft)}
      {textBtn('设为草稿', 'contained', () => {
        if (
          props.confirmAdoptDraft &&
          !window.confirm(
            '设为草稿会用该历史版本覆盖当前草稿的未发布变更，继续？'
          )
        )
          return
        props.useViewedRevisionAsDraft()
      })}
    </>
  )
}
