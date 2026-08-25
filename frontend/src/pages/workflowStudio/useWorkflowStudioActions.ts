import { useState } from 'react'
import { publishWorkflowDraft, validateWorkflowDraft } from '../../api'
import { useValidationFeedback } from './useValidationFeedback'
import type { UseWorkflowStudioDraftResult } from './useWorkflowStudioDraft'
import type { UseWorkflowDraftCompareResult } from './useWorkflowDraftCompare'

type ActionState = 'idle' | 'validating' | 'publishing'

export type UseWorkflowStudioActionsResult = {
  actionState: ActionState
  validationErrors: string[]
  validationMessage: string
  reviewDialogOpen: boolean
  canPublish: boolean
  validateDraft: () => Promise<void>
  publishDraft: () => Promise<void>
  requestPublish: () => void
  closeReviewDialog: () => void
}
export function useWorkflowStudioActions(
  workspaceId: string | undefined,
  draft: UseWorkflowStudioDraftResult,
  reload: () => Promise<void>,
  compare: UseWorkflowDraftCompareResult
): UseWorkflowStudioActionsResult {
  const [actionState, setActionState] = useState<ActionState>('idle')
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const { validationErrors, validationMessage, report } = useValidationFeedback(
    draft.definitionYaml
  )
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
  async function validateDraft() {
    if (!workspaceId) return
    setActionState('validating')
    try {
      const result = await validateWorkflowDraft(
        workspaceId,
        draft.definitionYaml
      )
      const message = result.valid ? '校验通过' : '校验失败'
      report(result.errors, message, result.valid ? 'success' : 'error')
    } catch (e) {
      const message = `校验失败：${(e instanceof Error && e.message) || '网络错误'}`
      report([], message, 'error')
    } finally {
      setActionState('idle')
    }
  }
  async function publishDraft() {
    if (!workspaceId) return
    setActionState('publishing')
    try {
      const result = await publishWorkflowDraft(
        workspaceId,
        draft.definitionYaml
      )
      if (result.valid) {
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
    validateDraft,
    publishDraft,
    requestPublish: () => {
      if (canPublish) setReviewDialogOpen(true)
    },
    closeReviewDialog: () => setReviewDialogOpen(false),
  }
}
