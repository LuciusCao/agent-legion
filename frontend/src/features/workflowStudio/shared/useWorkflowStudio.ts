import { useAgentCatalog } from '../inspector/useAgentCatalog'
import { useStudioDag } from '../canvas/useStudioDag'
import {
  buildStudioRevisionActions,
  useStudioNodeSelection,
} from '../inspector/useStudioNodeSelection'
import { useWorkflowDraftCompare } from './useWorkflowDraftCompare'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { useWorkflowStudioData } from './useWorkflowStudioData'
import { useWorkflowStudioDraftStore } from './useWorkflowStudioDraftStore'
import { isDefinitionDirty } from './workflowStudioModel'

export function useWorkflowStudio(workspaceId: string | undefined) {
  const {
    loadState,
    workflow,
    revision,
    revisions,
    originalYaml,
    reload,
    fetchRevisionDetail,
  } = useWorkflowStudioData(workspaceId)
  const catalog = useAgentCatalog(workspaceId)
  const draft = useWorkflowStudioDraftStore(
    workspaceId,
    originalYaml,
    workflow,
    revision,
    fetchRevisionDetail
  )
  // 不按 viewMode 门控的 dirty：revision 模式下 compare 继续运行，顶栏
  // 「草稿有未发布更改」才持续可见；画布 overlay 按 viewMode 另行门控。
  const compare = useWorkflowDraftCompare(
    workspaceId,
    draft.draftYaml,
    isDefinitionDirty(originalYaml, draft.draftYaml),
    loadState === 'empty'
  )
  const actions = useWorkflowStudioActions(workspaceId, draft, reload, compare)
  const { nodes, edges } = useStudioDag(
    draft.visibleWorkflow,
    catalog.agents,
    draft.viewMode === 'draft' ? compare.compareSummary : null
  )
  const { selectedNodeKey, setSelectedNodeKey } = useStudioNodeSelection(
    workspaceId,
    nodes
  )
  const revisionActions = buildStudioRevisionActions(draft, setSelectedNodeKey)
  return {
    loadState,
    actionState: actions.actionState,
    workflow: draft.visibleWorkflow,
    revision: draft.visibleRevision,
    activeRevision: revision,
    revisions,
    agentCatalog: catalog.agents,
    agentCatalogError: catalog.loadError,
    retryAgentCatalog: catalog.retry,
    definitionYaml: draft.definitionYaml,
    setDefinitionYaml: draft.setDraftYaml,
    selectedNodeKey,
    setSelectedNodeKey,
    validationErrors: actions.validationErrors,
    validationMessage: actions.validationMessage,
    dirty: draft.dirty,
    canSubmit: draft.canSubmit,
    draftSave: draft.draftSave,
    flushDraftSave: draft.flushDraftSave,
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
    ...revisionActions,
  }
}
