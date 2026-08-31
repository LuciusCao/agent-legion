import { Button, Typography } from '@mui/material'
import { draftSaveText } from './useWorkflowDraftPersistence'
import type { DraftSaveState } from './useWorkflowDraftPersistence'
import { useStudioState } from './studioStateContext'

type Props = {
  save: DraftSaveState | undefined
  readOnly: boolean
  onSaveDraft: () => void
}

/** 草稿保存状态文本 + 手动「保存草稿」按钮：五态可见（未保存更改/保存中…/
 * 已保存 HH:MM/保存失败将自动重试/服务不可用警示），失败态用警示色常驻。
 * 按钮仅在有未落盘内容（pending/error）时可点，点击立即 flush 落盘。 */
export function WorkflowStudioDraftSaveControl({
  save,
  readOnly,
  onSaveDraft,
}: Props) {
  const text = draftSaveText(save)
  const isWarning = save?.loadError === true || save?.status === 'error'
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
      {!readOnly ? (
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

/** 顶栏接线：从 Studio context 取草稿保存状态与 flush 动作（替代原 meta
 * tooltip 的低噪暴露）。 */
export function WorkflowStudioDraftSaveControlContainer() {
  const studio = useStudioState()
  return (
    <WorkflowStudioDraftSaveControl
      save={studio.draftSave}
      readOnly={studio.readOnly}
      onSaveDraft={() => studio.flushDraftSave()}
    />
  )
}
