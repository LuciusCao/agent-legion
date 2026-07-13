import { WorkflowDagFullscreenDialog } from './components/WorkflowDagFullscreenDialog'
import { WorkflowPublishReviewDialog } from './components/WorkflowPublishReviewDialog'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'

export function WorkflowStudioLayoutDialogs(props: StudioLayoutProps) {
  return (
    <>
      <WorkflowPublishReviewDialog
        open={props.reviewDialogOpen}
        workflowKey={props.workflow?.key ?? null}
        activeRevision={props.revision}
        nextVersion={(props.revision?.version ?? 0) + 1}
        definitionHash={props.revision?.definition_hash ?? null}
        summary={props.compareSummary}
        onConfirm={async () => {
          props.closeReviewDialog()
          await props.publishDraft()
          props.onShowChanges()
        }}
        onCancel={props.closeReviewDialog}
      />
      <WorkflowDagFullscreenDialog
        open={props.dagFullscreenOpen}
        workflow={props.workflow}
        executorCatalog={props.executorCatalog}
        selectedNode={props.selectedNodeKey}
        onSelectedNodeChange={props.setSelectedNodeKey}
        onClose={() => props.setDagFullscreenOpen(false)}
      />
    </>
  )
}
