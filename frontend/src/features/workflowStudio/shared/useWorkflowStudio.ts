import { useAgentCatalog } from '../inspector/useAgentCatalog'
import { useStudioDag } from '../canvas/useStudioDag'
import {
  buildStudioRevisionActions,
  useStudioNodeSelection,
} from '../inspector/useStudioNodeSelection'
import { useStudioRuntimeChannel } from './useStudioRuntimeChannel'
import { useWorkflowStudioData } from './useWorkflowStudioData'
import { useWorkflowStudioServerDraft } from './useWorkflowStudioServerDraft'

export function useWorkflowStudio(workspaceId: string | undefined) {
  const data = useWorkflowStudioData(workspaceId)
  const { loadState, revision, revisions, originalYaml } = data
  const catalog = useAgentCatalog(workspaceId)
  const { draft, compare, actions } = useWorkflowStudioServerDraft(
    workspaceId,
    data
  )
  // 运行通道：runtime 字段即改即存（不产生 revision），与结构发布通道并列。
  const runtimeChannel = useStudioRuntimeChannel({
    workspaceId,
    originalYaml,
    draftYaml: draft.draftYaml,
    setDraftYaml: draft.setDraftYaml,
    markRuntimeChannelSaved: draft.markRuntimeChannelSaved,
  })
  const { nodes, edges } = useStudioDag(
    draft.visibleWorkflow,
    catalog.agents,
    draft.viewMode === 'draft' ? compare.compareSummary : null
  )
  const { selectedNodeKey, setSelectedNodeKey, focusNonce, requestNodeFocus } =
    useStudioNodeSelection(workspaceId, nodes)
  return {
    loadState,
    publishing: actions.publishing,
    validating: actions.validating,
    workflow: draft.visibleWorkflow,
    revision: draft.visibleRevision,
    activeRevision: revision,
    revisions,
    agentCatalog: catalog.agents,
    agentCatalogError: catalog.loadError,
    retryAgentCatalog: catalog.retry,
    definitionYaml: draft.definitionYaml,
    /* 空内容冲突的「采用」需要 canonical 原文：无草稿可采用时回到已发布版本。 */
    originalYaml,
    // 保存状态机跟踪的草稿本体（revision 模式下 ≠ definitionYaml）。
    draftYaml: draft.draftYaml,
    setDefinitionYaml: draft.setDraftYaml,
    selectedNodeKey,
    setSelectedNodeKey,
    focusNonce,
    requestNodeFocus,
    validationErrors: actions.validationErrors,
    validationMessage: actions.validationMessage,
    publishError: actions.publishError,
    dirty: draft.dirty,
    canSubmit: draft.canSubmit,
    draftSave: draft.draftSave,
    flushDraftSave: draft.flushDraftSave,
    // agent 发布确认与手动发布共用「登记发布原文」收尾。
    markDraftPublished: draft.markDraftPublished,
    adoptServerDraft: draft.adoptServerDraft,
    resolveConflict: draft.resolveConflict,
    canPublish: actions.canPublish,
    retryValidation: actions.retryValidation,
    createsRevision: compare.compareSummary?.createsRevision ?? true,
    publishDraft: actions.publishDraft,
    requestPublish: actions.requestPublish,
    resetDefinition: () => draft.setDraftYaml(originalYaml),
    nodes,
    edges,
    compareState: compare.compareState,
    retryCompare: compare.retry,
    compareErrors: compare.compareErrors,
    compareSummary: compare.compareSummary,
    ...runtimeChannel,
    reviewDialogOpen: actions.reviewDialogOpen,
    reviewStale: actions.reviewStale,
    closeReviewDialog: actions.closeReviewDialog,
    viewMode: draft.viewMode,
    selectedRevisionId: draft.selectedRevisionId,
    readOnly: draft.readOnly,
    hasPreservedDraft: draft.hasPreservedDraft,
    isLoadingRevision: draft.isLoadingRevision,
    revisionLoadError: draft.revisionLoadError,
    ...buildStudioRevisionActions(draft, setSelectedNodeKey),
  }
}
