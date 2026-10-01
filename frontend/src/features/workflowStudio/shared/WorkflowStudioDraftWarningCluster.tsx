import { Button, Tooltip, Typography } from '@mui/material'
import { MaterialIcon } from '../../../components/MaterialIcon'
import islandStyles from './StudioCanvasIslands.module.css'

type ConflictProps = {
  /** 冲突警示全文（⚠ 图标的 tooltip + 宽屏长文案）。 */
  text: string | null
  readOnly: boolean
  onAdoptServer?: () => void
  onKeepMine?: () => void
  /** 抽屉内横幅用（轮 4 P2-F）：不挂窄屏隐藏类（抽屉在窄屏全宽覆盖，
   * secondary 规则只该管岛面）。 */
  plain?: boolean
}

/** 草稿保存警示簇（从 WorkflowStudioDraftSaveControl 拆出保体积预算）：
 * - 冲突簇（#633 + codex 轮 3 P1）：⚠ 图标（tooltip 载全文）+ 长文案
 *   （挂 island secondary——窄屏让位给 ⚠）+ 显式二选一操作（采用 Agent
 *   版本 / 保留本页编辑）。窄屏恒可见可操作——否则窄屏冲突无解、编辑会丢。
 * - 传输失败簇（codex 轮 4 P1-3 + 轮 5 P2）：loadError/PUT 重试耗尽
 *   终态的 ⚠ 警示 + error 态显式「重试保存」出口（controller 不再自行
 *   调度，网络恢复后用户不改内容也需要恢复路径）。 */
type WarningTextProps = {
  text: string | null
  /** 抽屉内横幅用：不挂窄屏隐藏类。 */
  plain?: boolean
  /** error 终态的显式重试（loadError 是读取侧失败，不给）。 */
  onRetrySave?: () => void
  showRetry?: boolean
}

export function WorkflowStudioDraftWarningText(props: WarningTextProps) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Tooltip title={props.text ?? ''}>
        <span style={{ display: 'inline-flex', color: '#c62828' }}>
          <MaterialIcon name="warning" fontSize="small" />
        </span>
      </Tooltip>
      <Typography
        variant="caption"
        color="error"
        sx={{ whiteSpace: 'nowrap' }}
        className={props.plain ? undefined : islandStyles.secondary}
      >
        {props.text}
      </Typography>
      {props.showRetry && props.onRetrySave ? (
        <Button size="small" variant="text" onClick={props.onRetrySave}>
          重试保存
        </Button>
      ) : null}
    </span>
  )
}
export function WorkflowStudioDraftConflictControl(props: ConflictProps) {
  const showActions = !props.readOnly && props.onAdoptServer && props.onKeepMine
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Tooltip title={props.text ?? ''}>
        <span style={{ display: 'inline-flex', color: '#c62828' }}>
          <MaterialIcon name="warning" fontSize="small" />
        </span>
      </Tooltip>
      <Typography
        variant="caption"
        color="error"
        sx={{ whiteSpace: 'nowrap' }}
        className={props.plain ? undefined : islandStyles.secondary}
      >
        {props.text}
      </Typography>
      {showActions ? (
        <>
          <Button
            size="small"
            variant="text"
            color="primary"
            onClick={props.onAdoptServer}
          >
            采用 Agent 版本
          </Button>
          <Button size="small" variant="text" onClick={props.onKeepMine}>
            保留本页编辑
          </Button>
        </>
      ) : null}
    </span>
  )
}
