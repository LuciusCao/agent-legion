import { WorkflowDagFullscreenDialog } from './components/WorkflowDagFullscreenDialog'
import { WorkflowPublishReviewDialog } from './components/WorkflowPublishReviewDialog'
import { useStudioState, useStudioView } from './studioStateContext'

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
          view.setCanvasMode('changes')
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
    </>
  )
}
