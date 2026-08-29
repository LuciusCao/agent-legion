import { WorkflowDagFullscreenDialog } from '../canvas/WorkflowDagFullscreenDialog'
import { WorkflowPublishReviewDialog } from '../validation/WorkflowPublishReviewDialog'
import { useStudioState, useStudioView } from './studioStateContext'
import { WorkflowStudioChangesDrawer } from '../validation/WorkflowStudioChangesDrawer'
import { WorkflowStudioYamlEditorDialog } from './WorkflowStudioYamlEditorDialog'

export function WorkflowStudioLayoutDialogs() {
  const studio = useStudioState()
  const view = useStudioView()
  return (
    <>
      <WorkflowPublishReviewDialog
        open={studio.reviewDialogOpen}
        workflowKey={studio.workflow?.key ?? null}
        activeRevision={studio.revision}
        nextVersion={(studio.revision?.version ?? 0) + 1}
        createsRevision={studio.createsRevision}
        definitionHash={studio.revision?.definition_hash ?? null}
        summary={studio.compareSummary}
        onConfirm={async () => {
          studio.closeReviewDialog()
          await studio.publishDraft()
          view.setChangesPanelOpen(true)
        }}
        onCancel={studio.closeReviewDialog}
      />
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
