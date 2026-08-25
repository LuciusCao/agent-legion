import { useMemo } from 'react'
import { useExecutorCatalog } from './useExecutorCatalog'
import { useStudioDag } from './useStudioDag'
import {
  buildStudioRevisionActions,
  useStudioNodeSelection,
} from './useStudioNodeSelection'
import { useWorkflowDraftCompare } from './useWorkflowDraftCompare'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { useWorkflowStudioData } from './useWorkflowStudioData'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'
import { applyCompareChanges } from './workflowStudioDagChanges'

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
  const catalog = useExecutorCatalog(workspaceId)
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
    draft.dirty,
    loadState === 'empty'
  )
  const actions = useWorkflowStudioActions(workspaceId, draft, reload, compare)
  const dag = useStudioDag(draft.visibleWorkflow, catalog.agents)
  // 草稿对比 diff 合并进 DAG：modified/removed 角标打在基线节点上，
  // added 以幽灵节点 + 幽灵边补入画布。
  const { nodes, edges } = useMemo(
    () => applyCompareChanges(dag.nodes, dag.edges, compare.compareSummary),
    [dag.nodes, dag.edges, compare.compareSummary]
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
