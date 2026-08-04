import type { useWorkflowStudio } from './useWorkflowStudio'
import type { useWorkflowStudioPageView } from './useWorkflowStudioPageView'
import { WorkflowStudioGlobalDialog } from './WorkflowStudioGlobalDialog'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'

type Studio = ReturnType<typeof useWorkflowStudio>
type View = ReturnType<typeof useWorkflowStudioPageView>

export function WorkflowStudioPageContent(props: {
  studio: Studio
  view: View
}) {
  const { studio, view } = props
  return (
    <>
      <WorkflowStudioLayout
        {...studio}
        dagFullscreenOpen={view.dagFullscreenOpen}
        setDagFullscreenOpen={view.setDagFullscreenOpen}
        onValidate={() => void studio.validateDraft()}
        onPublish={() => void studio.requestPublish()}
        onReset={studio.resetDefinition}
        onShowChanges={() => view.setGlobalMode('changes')}
      />
      <WorkflowStudioGlobalDialog
        mode={view.globalMode}
        studio={studio}
        onClose={() => view.setGlobalMode(null)}
      />
    </>
  )
}
