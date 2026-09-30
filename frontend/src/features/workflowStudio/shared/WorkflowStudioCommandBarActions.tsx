import { Button, IconButton, Tooltip } from '@mui/material'
import { MaterialIcon } from '../../../components/MaterialIcon'
import { WorkflowStudioActionsOverflowMenu } from './WorkflowStudioActionsOverflowMenu'

type Props = {
  readOnly: boolean
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  canPublish: boolean
  createsRevision?: boolean
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

/** 指挥中心岛的生命周期动作组（#799 重组精修）：校验收成图标按钮 +
 * tooltip、发布保持 contained 文字主按钮、重置收进 ⋮ 溢出菜单（低频
 * 破坏性动作，PreviewGovernanceMenu 同款模式）；只读态（返回/设为草稿）
 * 保持文字按钮不动。 */
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

  return (
    <>
      <Tooltip title="校验">
        {/* disabled 时 Tooltip 需要 wrapper span（MUI 约定，否则告警） */}
        <span>
          <IconButton
            size="small"
            aria-label="校验"
            disabled={!props.canSubmit || !idle}
            onClick={props.onValidate}
          >
            <MaterialIcon name="checklist" fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Button
        size="small"
        variant="contained"
        disabled={!props.canPublish || !idle}
        onClick={props.onPublish}
      >
        {props.createsRevision === false ? '保存运行配置' : '发布新版本'}
      </Button>
      <WorkflowStudioActionsOverflowMenu
        disabled={!props.dirty || !idle}
        onReset={props.onReset}
      />
    </>
  )
}
