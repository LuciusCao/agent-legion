import { WorkflowDagFullscreenDialog } from '../canvas/WorkflowDagFullscreenDialog'
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
        {...reviewDialogProps(studio)}
        onConfirm={async () => {
          studio.closeReviewDialog()
          await studio.publishDraft()
          view.setChangesPanelOpen(true)
        }}
        onCancel={studio.closeReviewDialog}
      />
      {/* #416：agent 发起的发布请求弹同一个确认对话框（独立组件承载，
          手动流程优先，两者不叠加；见 AgentPublishRequestDialog）。 */}
      <AgentPublishRequestDialog />
      <WorkflowDagFullscreenDialog
        open={view.dagFullscreenOpen}
        nodes={studio.nodes}
        edges={studio.edges}
        selectedNode={studio.selectedNodeKey}
        onSelectedNodeChange={studio.setSelectedNodeKey}
        onClose={() => view.setDagFullscreenOpen(false)}
      />
      <WorkflowStudioChangesDrawer />
      <WorkflowStudioYamlEditorDialog />
    </>
  )
}
