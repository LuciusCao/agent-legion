import { Button, Tooltip, Typography } from '@mui/material'
import { MaterialIcon } from '../../../components/MaterialIcon'
import islandStyles from './StudioCanvasIslands.module.css'

type Props = {
  /** 冲突警示全文（⚠ 图标的 tooltip + 宽屏长文案）。 */
  text: string | null
  readOnly: boolean
  onAdoptServer?: () => void
  onKeepMine?: () => void
  /** 抽屉内横幅用（轮 4 P2-F）：不挂窄屏隐藏类（抽屉在窄屏全宽覆盖，
   * secondary 规则只该管岛面）。 */
  plain?: boolean
}

/** 草稿 CAS 冲突簇（#633 + codex 轮 3 P1，从 WorkflowStudioDraftSaveControl
 * 拆出保体积预算）：⚠ 图标（tooltip 载全文）+ 长文案（挂 island
 * secondary——窄屏让位给 ⚠）+ 显式二选一操作（采用 Agent 版本 / 保留本页
 * 编辑）。窄屏恒可见可操作——否则窄屏冲突无解、编辑会丢。 */
export function WorkflowStudioDraftConflictControl(props: Props) {
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
