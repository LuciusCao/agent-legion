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
    // #426 review P2 → codex 终轮 P2：两份目录查询的 settle 信号下发到
    // 节点详情，由节点级按 capability 组合出内联 Agent 编辑器的门控状态
    // （catalog 命中 published 即 ready，未命中须等 definitions settle）。
    agentCatalogSettle: catalog.settle,
    definitionYaml: draft.definitionYaml,
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
