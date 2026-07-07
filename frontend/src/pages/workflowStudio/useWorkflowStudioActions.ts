import { useState } from 'react'
import { publishWorkflowDraft, validateWorkflowDraft } from '../../api'
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
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [validationMessage, setValidationMessage] = useState('')
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
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
      setValidationErrors(result.errors)
      setValidationMessage(result.valid ? '校验通过' : '校验失败')
    } catch (e) {
      setValidationErrors([])
      setValidationMessage(
        `校验失败：${(e instanceof Error && e.message) || '网络错误'}`
      )
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
      setValidationErrors(result.errors)
      if (result.valid) {
        await reload()
        setValidationMessage('发布成功')
      } else {
        setValidationMessage('发布失败')
      }
    } catch (e) {
      setValidationErrors([])
      setValidationMessage(
        `发布失败：${(e instanceof Error && e.message) || '网络错误'}`
      )
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
