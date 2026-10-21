import { useAgentCatalog } from '../inspector/useAgentCatalog'
import { useStudioDag } from '../canvas/useStudioDag'
import {
  buildStudioRevisionActions,
  useStudioNodeSelection,
} from '../inspector/useStudioNodeSelection'
import { useWorkflowDraftCompare } from './useWorkflowDraftCompare'
import { useStudioRuntimeChannel } from './useStudioRuntimeChannel'
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
  // M48 运行通道：runtime 字段即改即存（不产生 revision），与结构发布通道并列。
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
    /* #1226 复审 P1：空内容冲突（发布删行后的 409）的「采用」需要
       canonical 原文——服务端无草稿可采用时，采用 = 回到已发布版本。 */
    originalYaml,
    // #1196：保存状态机跟踪的草稿本体（revision 模式下 ≠ definitionYaml）。
    draftYaml: draft.draftYaml,
    setDefinitionYaml: draft.setDraftYaml,
    selectedNodeKey,
    setSelectedNodeKey,
    focusNonce,
    requestNodeFocus,
    validationErrors: actions.validationErrors,
    validationMessage: actions.validationMessage,
    dirty: draft.dirty,
    canSubmit: draft.canSubmit,
    draftSave: draft.draftSave,
    flushDraftSave: draft.flushDraftSave,
    // #1122：agent 发布确认管道复用「登记发布原文」收尾（见
    // AgentPublishRequestDialog），与手动 publishDraft 同一机制。
    markDraftPublished: draft.markDraftPublished,
    adoptServerDraft: draft.adoptServerDraft, // kimi review P1-2：冲突出口
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
    // M48 运行通道字段（runtimeChangedKeys / saveRuntimeConfig / 等六项）
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
