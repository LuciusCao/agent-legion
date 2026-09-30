import { Button, Tooltip } from '@mui/material'
import { WorkflowStudioReadOnlyActions } from './WorkflowStudioReadOnlyActions'

type Props = {
  readOnly: boolean
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canPublish: boolean
  createsRevision?: boolean
  /** 发布禁用的说明（codex 轮 3 P2：canPublish 已绑定「当前 YAML 校验
   * 通过」；未校验/校验失败时由调用方给原因文案，经 Tooltip 露出）。 */
  publishTooltip?: string
  /** 轮 4 P2-E：「设为草稿」会用历史版本覆盖当前草稿——草稿有未发布
   * 变更时必须确认（调用方按 dirty/compare 计数判定）。 */
  confirmAdoptDraft?: boolean
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
    return (
      <WorkflowStudioReadOnlyActions
        idle={idle}
        confirmAdoptDraft={props.confirmAdoptDraft}
        backToDraft={props.backToDraft}
        useViewedRevisionAsDraft={props.useViewedRevisionAsDraft}
      />
    )
  }

  const publishDisabled = !props.canPublish || !idle
  return (
    <>
      <Tooltip title={props.publishTooltip ?? ''}>
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
