import { WorkflowPublishReviewDialog } from '../validation/WorkflowPublishReviewDialog'
import { useStudioState, useStudioView } from './studioStateContext'
import { WorkflowStudioChangesDrawer } from '../validation/WorkflowStudioChangesDrawer'
import { WorkflowStudioYamlEditorDialog } from './WorkflowStudioYamlEditorDialog'
import {
  AgentPublishRequestDialog,
  reviewDialogProps,
} from './AgentPublishRequestDialog'

export function WorkflowStudioLayoutDialogs() {
  const studio = useStudioState()
  const view = useStudioView()
  return (
    <>
      <WorkflowPublishReviewDialog
        open={studio.reviewDialogOpen}
        contentStale={studio.reviewStale}
        {...reviewDialogProps(studio)}
        onConfirm={async () => {
          // 轮 7 P1 兜底：确认键禁用只拦 UI 点击，提交前按当前 canPublish
          // 重查（冲突/内容漂移在确认框打开期间都可能后到达）。
          if (studio.reviewStale || !studio.canPublish) return
          studio.closeReviewDialog()
          await studio.publishDraft()
          view.setChangesPanelOpen(true)
        }}
        onCancel={studio.closeReviewDialog}
      />
      {/* #416：agent 发起的发布请求弹同一个确认对话框（独立组件承载，
          手动流程优先，两者不叠加；见 AgentPublishRequestDialog）。 */}
      <AgentPublishRequestDialog />
      <WorkflowStudioChangesDrawer />
      <WorkflowStudioYamlEditorDialog />
    </>
  )
}
