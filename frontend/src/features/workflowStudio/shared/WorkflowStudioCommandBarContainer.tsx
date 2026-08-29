import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'
import { useStudioState, useStudioView } from './studioStateContext'

/** 顶栏容器：从 Studio context 取状态与视图动作，组装 CommandBar。 */
export function WorkflowStudioCommandBarContainer() {
  const studio = useStudioState()
  const view = useStudioView()
  return (
    <WorkflowStudioCommandBar
      revision={studio.revision}
      revisions={studio.revisions}
      activeRevision={studio.activeRevision}
      viewMode={studio.viewMode}
      dirty={studio.dirty}
      readOnly={studio.readOnly}
      hasPreservedDraft={studio.hasPreservedDraft}
      compareSummary={studio.compareSummary}
      compareState={studio.compareState}
      draftSave={studio.draftSave}
      actionState={studio.actionState}
      canSubmit={studio.canSubmit}
      canPublish={studio.canPublish}
      selectedRevisionId={studio.selectedRevisionId}
      isLoadingRevision={studio.isLoadingRevision}
      revisionLoadError={studio.revisionLoadError}
      onSelectRevision={studio.selectRevision}
      onValidate={() => void view.validateAndShowResult()}
      onPublish={() => void studio.requestPublish()}
      onReset={studio.resetDefinition}
      onShowChanges={() => view.setChangesPanelOpen(true)}
      backToDraft={studio.backToDraft}
      useViewedRevisionAsDraft={studio.useViewedRevisionAsDraft}
    />
  )
}
