import type { useWorkflowStudio } from './useWorkflowStudio'
import type { useWorkflowStudioPageView } from './useWorkflowStudioPageView'
import { StudioNavContext, type StudioNav } from './workflowStudioNav'
import { WorkflowStudioGlobalDialog } from './WorkflowStudioGlobalDialog'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'

type Studio = ReturnType<typeof useWorkflowStudio>
type View = ReturnType<typeof useWorkflowStudioPageView>

export function WorkflowStudioPageContent(props: {
  studio: Studio
  view: View
}) {
  const { studio, view } = props
  const nav: StudioNav = {
    openAgent: (agentId) => view.openPanel('agents', agentId),
    openExecutor: (executorId) => view.openPanel('executors', executorId),
  }
  return (
    <StudioNavContext.Provider value={nav}>
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
        panelFocus={view.panelFocus}
        onClose={() => view.setGlobalMode(null)}
      />
    </StudioNavContext.Provider>
  )
}
