import { Button, Typography } from '@mui/material'
import { draftSaveText } from './useWorkflowDraftPersistence'
import type { DraftSaveState } from './useWorkflowDraftPersistence'
import { useStudioState } from './studioStateContext'

type Props = {
  save: DraftSaveState | undefined
  readOnly: boolean
  onSaveDraft: () => void
  /* kimi review P1-2：冲突态动作。onAdoptServer = 采用服务端（Agent）版本
   * 写入画布（controller 经 hydrate 推进基线、清除冲突）；onKeepMine =
   * 看过警示后保留本页编辑并立即保存（以已推进的基线竞争）。 */
  onAdoptServer?: () => void
  onKeepMine?: () => void
}

/* 草稿保存状态文本 + 手动「保存草稿」按钮：可见态含未保存更改/保存中…/
   已保存 HH:MM/保存失败将自动重试/服务不可用警示/#633 冲突警示（服务端
   草稿被 Agent 或其它标签页推进，本页编辑未落盘），警示态用警示色常驻。
   kimi review P1-2/P2-4：冲突态不再是一键秒消——自动保存挂起，用户显式
   二选一（采用服务端版本 / 保留本页编辑），普通「保存草稿」按钮在冲突
   态让位给这两个动作。 */
export function WorkflowStudioDraftSaveControl({
  save,
  readOnly,
  onSaveDraft,
  onAdoptServer,
  onKeepMine,
}: Props) {
  const text = draftSaveText(save)
  const isWarning =
    save?.loadError === true ||
    save?.status === 'error' ||
    save?.conflict === true
  const inConflict = save?.conflict === true
  const canSave = save?.status === 'pending' || save?.status === 'error'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
      {text ? (
        <Typography
          variant="caption"
          color={isWarning ? 'error' : 'text.secondary'}
          sx={{ whiteSpace: 'nowrap' }}
        >
          {text}
        </Typography>
      ) : null}
      {!readOnly && inConflict && onAdoptServer && onKeepMine ? (
        <>
          <Button
            size="small"
            variant="text"
            color="primary"
            onClick={onAdoptServer}
          >
            采用 Agent 版本
          </Button>
          <Button size="small" variant="text" onClick={onKeepMine}>
            保留本页编辑
          </Button>
        </>
      ) : null}
      {!readOnly && !inConflict ? (
        <Button
          size="small"
          variant="text"
          disabled={!canSave}
          onClick={onSaveDraft}
        >
          保存草稿
        </Button>
      ) : null}
    </span>
  )
}

/* 顶栏接线：从 Studio context 取草稿保存状态与 flush 动作（替代原 meta
   tooltip 的低噪暴露）。 */
export function WorkflowStudioDraftSaveControlContainer() {
  const studio = useStudioState()
  return (
    <WorkflowStudioDraftSaveControl
      save={studio.draftSave}
      readOnly={studio.readOnly}
      onSaveDraft={() => studio.flushDraftSave()}
      onAdoptServer={() => {
        const conflict = studio.draftSave?.conflictDraftYaml
        if (conflict == null) {
          studio.resolveConflict(false)
          return
        }
        studio.adoptServerDraft(conflict, studio.draftSave?.savedAt ?? null)
      }}
      onKeepMine={() => studio.resolveConflict(true)}
    />
  )
}
