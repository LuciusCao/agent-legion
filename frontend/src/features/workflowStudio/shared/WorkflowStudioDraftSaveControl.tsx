import { Typography } from '@mui/material'
import { draftSaveText } from './useWorkflowDraftPersistence'
import type { DraftSaveState } from './useWorkflowDraftPersistence'
import { useStudioState } from './studioStateContext'
import {
  WorkflowStudioDraftConflictControl,
  WorkflowStudioDraftWarningText,
} from './WorkflowStudioDraftWarningCluster'
import islandStyles from './StudioCanvasIslands.module.css'

type Props = {
  save: DraftSaveState | undefined
  readOnly: boolean
  /* kimi review P1-2：冲突态动作。onAdoptServer = 采用服务端（Agent）版本
   * 写入画布（controller 经 hydrate 推进基线、清除冲突）；onKeepMine =
   * 看过警示后保留本页编辑并立即保存（以已推进的基线竞争）。 */
  onAdoptServer?: () => void
  onKeepMine?: () => void
  /** 抽屉内警示横幅模式（轮 4 P2-F）：只渲染警示态（冲突/终态失败/服务
   * 不可用），瞬态「保存中…」不出现；长文案不挂窄屏隐藏类。 */
  warningsOnly?: boolean
  /** codex 轮 5 P2：error 终态（PUT 重试耗尽）的显式重试出口——controller
   * 不再自行调度，网络恢复后用户不改内容也需要恢复路径；点击重新调度
   * 当前内容（接线层调 flushDraftSave：error 态会重新 schedule 再 flush）。 */
  onRetrySave?: () => void
}

/* 草稿保存状态文本（#804 定案：手动「保存草稿」按钮退役——自动保存已
   覆盖，成功即隐；可见态只剩 保存中…/保存失败将自动重试/服务不可用警示/
   #633 冲突警示，警示态用警示色常驻。kimi review P1-2/P2-4：冲突态不再
   是一键秒消——自动保存挂起，用户显式二选一（采用服务端版本 / 保留本页
   编辑）。
   codex 轮 3 P1：窄屏降级不能一刀切——瞬态文本（保存中/失败重试）窄屏
   隐藏无妨，但冲突警示与冲突操作出口必须窄屏可见可操作（否则窄屏冲突
   无解、编辑会丢）：冲突簇恒在，长文案窄屏收成 ⚠ 图标（tooltip 载全文）；
   瞬态文本挂 island 的 secondary 类（窄屏隐藏）。 */
export function WorkflowStudioDraftSaveControl({
  save,
  readOnly,
  onAdoptServer,
  onKeepMine,
  warningsOnly,
  onRetrySave,
}: Props) {
  const text = draftSaveText(save)
  const inConflict = save?.conflict === true
  // codex 轮 4 P1-3：持久化不可用（loadError）与终态失败（error，重试
  // 耗尽）属用户必须知情的警示——窄屏同样保留（⚠ 恒可见 + tooltip 载
  // 全文，长文案窄屏让位）；瞬态「保存中…」才允许窄屏隐藏。
  const isWarning =
    save?.loadError === true || save?.status === 'error' || inConflict
  const showConflictActions =
    !readOnly && inConflict && onAdoptServer && onKeepMine
  // #804 定案：无可见内容时不渲染——岛的 flex gap 会给空壳 span 留死白。
  if (!text && !showConflictActions) return null
  if (inConflict) {
    // 冲突簇拆在 WorkflowStudioDraftConflictControl（体积预算）：窄屏恒
    // 可见可操作（⚠ + 二选一按钮），长文案窄屏收成 ⚠ tooltip。
    return (
      <WorkflowStudioDraftConflictControl
        text={text}
        readOnly={readOnly}
        onAdoptServer={onAdoptServer}
        onKeepMine={onKeepMine}
        plain={warningsOnly}
      />
    )
  }
  if (warningsOnly && !isWarning) return null
  if (isWarning) {
    // 警示簇（⚠ + 文案 + error 态重试出口）在 WorkflowStudioDraftWarningCluster。
    return (
      <WorkflowStudioDraftWarningText
        text={text}
        plain={warningsOnly}
        showRetry={save?.status === 'error'}
        onRetrySave={onRetrySave}
      />
    )
  }
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ whiteSpace: 'nowrap' }}
      className={islandStyles.secondary}
    >
      {text}
    </Typography>
  )
}

/* 保存警示的回调接线（容器与抽屉警示横幅共用，轮 4 P2-F / 轮 5 P2）：
   冲突二选一 + error 终态的显式重试（flushDraftSave 在 error 态会重新
   schedule 当前内容再 flush——persistence 层既有语义）。 */
export function useDraftSaveConflictActions() {
  const studio = useStudioState()
  return {
    onAdoptServer: () => {
      const conflict = studio.draftSave?.conflictDraftYaml
      if (conflict == null) {
        studio.resolveConflict(false)
        return
      }
      studio.adoptServerDraft(conflict, studio.draftSave?.savedAt ?? null)
    },
    onKeepMine: () => studio.resolveConflict(true),
    onRetrySave: () => void studio.flushDraftSave(),
  }
}

/* 顶栏接线：从 Studio context 取草稿保存状态与冲突动作（替代原 meta
   tooltip 的低噪暴露）。 */
export function WorkflowStudioDraftSaveControlContainer() {
  const studio = useStudioState()
  const actions = useDraftSaveConflictActions()
  return (
    <WorkflowStudioDraftSaveControl
      save={studio.draftSave}
      readOnly={studio.readOnly}
      {...actions}
    />
  )
}
