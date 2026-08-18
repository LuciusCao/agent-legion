import { useState } from 'react'
import { useExecutorCatalog } from './useExecutorCatalog'
import { useStudioDag } from './useStudioDag'
import { useWorkflowDraftCompare } from './useWorkflowDraftCompare'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { useWorkflowStudioData } from './useWorkflowStudioData'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

export function useWorkflowStudio(workspaceId: string | undefined) {
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const {
    loadState,
    workflow,
    revision,
    revisions,
    originalYaml,
    reload,
    fetchRevisionDetail,
  } = useWorkflowStudioData(workspaceId)
  const { agents: agentCatalog } = useExecutorCatalog(workspaceId)
  const draft = useWorkflowStudioDraft(
    workspaceId,
    originalYaml,
    workflow,
    revision,
    fetchRevisionDetail
  )
  const compare = useWorkflowDraftCompare(
    workspaceId,
    draft.draftYaml,
    draft.dirty
  )
  const actions = useWorkflowStudioActions(workspaceId, draft, reload, compare)
  const { nodes, edges } = useStudioDag(draft.visibleWorkflow, agentCatalog)
  return {
    loadState,
    actionState: actions.actionState,
    workflow: draft.visibleWorkflow,
    revision: draft.visibleRevision,
    activeRevision: revision,
    revisions,
    agentCatalog,
    definitionYaml: draft.definitionYaml,
    setDefinitionYaml: draft.setDraftYaml,
    selectedNodeKey,
    setSelectedNodeKey,
    validationErrors: actions.validationErrors,
    validationMessage: actions.validationMessage,
    dirty: draft.dirty,
    canSubmit: draft.canSubmit,
    canPublish: actions.canPublish,
    createsRevision: compare.compareSummary?.createsRevision ?? true,
    validateDraft: actions.validateDraft,
    publishDraft: actions.publishDraft,
    requestPublish: actions.requestPublish,
    resetDefinition: () => draft.setDraftYaml(originalYaml),
    nodes,
    edges,
    compareState: compare.compareState,
    compareErrors: compare.compareErrors,
    compareSummary: compare.compareSummary,
    reviewDialogOpen: actions.reviewDialogOpen,
    closeReviewDialog: actions.closeReviewDialog,
    viewMode: draft.viewMode,
    selectedRevisionId: draft.selectedRevisionId,
    readOnly: draft.readOnly,
    hasPreservedDraft: draft.hasPreservedDraft,
    isLoadingRevision: draft.isLoadingRevision,
    revisionLoadError: draft.revisionLoadError,
    selectRevision: async (revisionId: string) => {
      await draft.selectRevision(revisionId)
      setSelectedNodeKey(null)
    },
    backToDraft: () => {
      draft.backToDraft()
      setSelectedNodeKey(null)
    },
    useViewedRevisionAsDraft: () => {
      draft.useViewedRevisionAsDraft()
      setSelectedNodeKey(null)
    },
  }
}
