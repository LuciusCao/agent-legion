import { Button, Tooltip } from '@mui/material'

type Props = {
  readOnly: boolean
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canPublish: boolean
  createsRevision?: boolean
  /** #804 定案：自动校验失败 → 发布禁用 + tooltip 说明（修复后保存会
   * 自动重新校验，通过后恢复可用）。 */
  validationFailed?: boolean
  onPublish: () => void
  onReset: () => void
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

/** 指挥中心岛的生命周期动作组（#804 定案）：校验按钮退役（改保存成功后
 * 自动静默校验，结果驱动状态 chip 与本组的发布门控）；发布保持
 * contained 文字主按钮；重置回到岛面——仅 dirty 时外露的 outlined 次级
 * 按钮（干净态消失，单一项的 ⋮ 溢出菜单随之退役）；只读态（返回/设为
 * 草稿）保持文字按钮不动。 */
export function WorkflowStudioCommandBarActions(props: Props) {
  const idle = props.actionState === 'idle'

  if (props.readOnly) {
    const textBtn = (
      label: string,
      variant: 'outlined' | 'contained',
      onClick: () => void
    ) => (
      <Button size="small" variant={variant} disabled={!idle} onClick={onClick}>
        {label}
      </Button>
    )
    return (
      <>
        {textBtn('返回', 'outlined', props.backToDraft)}
        {textBtn('设为草稿', 'contained', props.useViewedRevisionAsDraft)}
      </>
    )
  }

  const publishDisabled = !props.canPublish || !idle || !!props.validationFailed
  return (
    <>
      <Tooltip
        title={props.validationFailed ? '校验失败，请修复后重新发布' : ''}
      >
        {/* disabled 时 Tooltip 需要 wrapper span（MUI 约定，否则告警） */}
        <span>
          <Button
            size="small"
            variant="contained"
            disabled={publishDisabled}
            onClick={props.onPublish}
          >
            {props.createsRevision === false ? '保存运行配置' : '发布'}
          </Button>
        </span>
      </Tooltip>
      {props.dirty ? (
        <Button
          size="small"
          variant="outlined"
          disabled={!idle}
          onClick={props.onReset}
        >
          重置
        </Button>
      ) : null}
    </>
  )
}
