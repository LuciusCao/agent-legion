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
  /** 审 A 发 B 守卫（#804 轮 4 P2-C）：确认框打开时捕获当时 YAML；
   * 打开期间草稿被后台换掉（agent turn-end 保存/reapply）→ true，
   * 确认键禁用，需关闭重审。 */
  reviewStale: boolean
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
  // P2-C：确认框打开那一刻的 YAML 快照（审阅对象）。
  const [reviewYaml, setReviewYaml] = useState<string | null>(null)
  const { validationErrors, validationMessage, report, reportSilent } =
    useValidationFeedback(draft.definitionYaml)
  const setValidating = useCallback(
    (on: boolean) => setActionState(on ? 'validating' : 'idle'),
    []
  )
  useDraftAutoValidation({
    workspaceId,
    saveState: draft.draftSave,
    canSubmit: draft.canSubmit,
    definitionYaml: draft.definitionYaml,
    validationMessage,
    validating: actionState === 'validating',
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
  /* codex 轮 3 P2：发布门控与当前 YAML 的校验结果绑定——只有当前内容明确
   * 校验通过才放行（hydrate 恢复的草稿不产生 saved 边沿、debounce 窗口内
   * 的内容，都按未校验处理；useDraftAutoValidation 会在落盘后补上校验）。
   * validationMessage 随 definitionYaml 变化即被 useValidationFeedback
   * 作废，「校验通过」必然属于当前内容。 */
  const canPublish =
    draft.canSubmit &&
    compareState !== 'loading' &&
    !hasBlockingCompareError &&
    hasCompareChanges &&
    validationMessage === '校验通过'
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
    reviewStale:
      reviewDialogOpen &&
      reviewYaml !== null &&
      reviewYaml !== draft.definitionYaml,
    publishDraft,
    requestPublish: () => {
      if (canPublish) {
        setReviewYaml(draft.definitionYaml)
        setReviewDialogOpen(true)
      }
    },
    closeReviewDialog: () => {
      setReviewDialogOpen(false)
      setReviewYaml(null)
    },
  }
}
