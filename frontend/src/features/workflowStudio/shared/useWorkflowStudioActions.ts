import { useCallback, useState } from 'react'
import { publishWorkflowDraft } from '../../../api'
import { useValidationFeedback } from '../validation/useValidationFeedback'
import { useDraftAutoValidation } from './useDraftAutoValidation'
import type { DraftSaveState } from './draftSaveTypes'
import type { UseWorkflowStudioDraftResult } from './useWorkflowStudioDraft'
import type { UseWorkflowDraftCompareResult } from './useWorkflowDraftCompare'

type ActionState = 'idle' | 'validating' | 'publishing'

/* #804 定案：手动「校验」按钮退役——草稿保存成功后自动静默校验
 * （useDraftAutoValidation），结果驱动左岛状态 chip；actions 层需要保存
 * 状态机的当前状态做触发边沿。 */
type DraftWithSave = UseWorkflowStudioDraftResult & {
  draftSave: DraftSaveState
}

export type UseWorkflowStudioActionsResult = {
  actionState: ActionState
  validationErrors: string[]
  validationMessage: string
  reviewDialogOpen: boolean
  canPublish: boolean
  publishDraft: () => Promise<void>
  requestPublish: () => void
  closeReviewDialog: () => void
}
export function useWorkflowStudioActions(
  workspaceId: string | undefined,
  draft: DraftWithSave,
  reload: () => Promise<void>,
  compare: UseWorkflowDraftCompareResult
): UseWorkflowStudioActionsResult {
  const [actionState, setActionState] = useState<ActionState>('idle')
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const { validationErrors, validationMessage, report, reportSilent } =
    useValidationFeedback(draft.definitionYaml)
  const setValidating = useCallback(
    (on: boolean) => setActionState(on ? 'validating' : 'idle'),
    []
  )
  useDraftAutoValidation({
    workspaceId,
    saveStatus: draft.draftSave.status,
    canSubmit: draft.canSubmit,
    definitionYaml: draft.definitionYaml,
    reportSilent,
    setValidating,
  })
  const { compareState, compareErrors, compareSummary } = compare
  const hasCompareChanges = Boolean(
    compareSummary?.nodeChanges.length ||
    compareSummary?.edgeChanges.length ||
    compareSummary?.intakeChanges.length ||
    compareSummary?.riskFlags.length
  )
  const hasBlockingCompareError = Boolean(
    compareErrors?.some(
      (error) => error.category === 'yaml' || error.category === 'schema'
    )
  )
  const canPublish =
    draft.canSubmit &&
    compareState !== 'loading' &&
    !hasBlockingCompareError &&
    hasCompareChanges
  async function publishDraft() {
    if (!workspaceId) return
    setActionState('publishing')
    try {
      const result = await publishWorkflowDraft(
        workspaceId,
        draft.definitionYaml
      )
      if (result.valid) {
        // #666：先登记发布的草稿原文再 reload——baseline sync 见到紧随的
        // canonical 基线变化时据此强制 reset，不误判为外部变更保留旧草稿。
        draft.markDraftPublished(draft.definitionYaml)
        await reload()
        report(result.errors, '保存成功', 'success')
      } else {
        report(result.errors, '保存失败', 'error')
      }
    } catch (e) {
      const message = `保存失败：${(e instanceof Error && e.message) || '网络错误'}`
      report([], message, 'error')
    } finally {
      setActionState('idle')
    }
  }
  return {
    actionState,
    validationErrors,
    validationMessage,
    reviewDialogOpen,
    canPublish,
    publishDraft,
    requestPublish: () => {
      if (canPublish) setReviewDialogOpen(true)
    },
    closeReviewDialog: () => setReviewDialogOpen(false),
  }
}
