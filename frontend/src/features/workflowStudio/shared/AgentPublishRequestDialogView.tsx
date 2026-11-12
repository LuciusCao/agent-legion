import { WorkflowPublishReviewDialog } from '../validation/WorkflowPublishReviewDialog'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import type { WorkflowRevisionSummary } from '../../../types'
import type { StudioState } from './studioStateContext'
import type { AgentPublishRequestState } from './useAgentPublishRequest'

export function AgentPublishRequestDialogView({
  studio,
  agentRequest,
  onConfirm,
}: {
  studio: StudioState
  agentRequest: AgentPublishRequestState
  onConfirm: () => Promise<void>
}) {
  if (agentRequest.pendingRequest === null) return null
  const open = !studio.reviewDialogOpen
  // #429 四轮 P3-2：轮询到 confirming 行（本 app 的 confirm 在途，或另一
  // tab/实例刚点过确认）时，对话框按「发布进行中」呈现——按钮禁用、关闭
  // 渠道静默，而不是让对话框消失。
  const polledConfirming = agentRequest.pendingRequest.status === 'confirming'
  return (
    <WorkflowPublishReviewDialog
      open={open}
      {...reviewDialogProps(studio)}
      confirming={polledConfirming || agentRequest.publishInFlight}
      canceling={agentRequest.canceling}
      // #542：发布面 advisory（skill 无机器契约）——poll 读取侧现算透传。
      advisories={agentRequest.pendingRequest.warnings ?? []}
      onConfirm={onConfirm}
      onCancel={() => void agentRequest.cancel()}
    />
  )
}

/** 手动与 agent 两个确认对话框共享的 props（workflow/revision/compare
 * 数据流）：两个对话框对同一份草稿展示同一份比对结果。 */
export function reviewDialogProps(studio: {
  revision?: WorkflowRevisionSummary | null
  createsRevision?: boolean
  compareSummary?: ChangeSummaryViewModel | null
  runtimeChangedKeys?: string[]
  lastRuntimeSavedKeys?: string[]
}) {
  return {
    activeRevision: studio.revision ?? null,
    nextVersion: (studio.revision?.version ?? 0) + 1,
    createsRevision: studio.createsRevision,
    definitionHash: studio.revision?.definition_hash ?? null,
    summary: studio.compareSummary ?? null,
    runtimeChangedKeys: studio.runtimeChangedKeys ?? [],
    runtimeSavedKeys: studio.lastRuntimeSavedKeys ?? [],
  }
}
