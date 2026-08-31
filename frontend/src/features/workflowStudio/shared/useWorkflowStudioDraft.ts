import { useMemo, useState } from 'react'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../../types'
import { workflowYamlToDefinitionRecord } from '../canvas/workflowYamlDraftRecord'
import { isDefinitionDirty } from './workflowStudioModel'
import { useDraftBaselineSync } from './useDraftBaselineSync'
import { useWorkflowStudioRevisionSelection } from './useWorkflowStudioRevisionSelection'
import {
  createDraftViewState,
  isRevisionReadOnly,
  type WorkflowStudioViewState,
} from './workflowStudioViewState'

export type UseWorkflowStudioDraftResult = {
  draftYaml: string
  setDraftYaml: (value: string) => void
  definitionYaml: string
  visibleWorkflow: WorkflowDefinitionRecord | null
  visibleRevision: WorkflowRevisionSummary | null
  readOnly: boolean
  dirty: boolean
  canSubmit: boolean
  viewMode: 'draft' | 'revision'
  selectedRevisionId: string | null
  hasPreservedDraft: boolean
  isLoadingRevision: boolean
  revisionLoadError: string | null
  selectRevision: (revisionId: string) => Promise<void>
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

export function useWorkflowStudioDraft(
  workspaceId: string | undefined,
  originalYaml: string,
  activeWorkflow: WorkflowDefinitionRecord | null,
  activeRevision: WorkflowRevisionSummary | null,
  fetchRevisionDetail: (
    revisionId: string
  ) => Promise<WorkflowRevisionDetailResponse>
): UseWorkflowStudioDraftResult {
  const [draftYaml, setDraftYaml] = useState('')
  const [viewState, setViewState] = useState<WorkflowStudioViewState>(
    createDraftViewState(null)
  )
  const {
    isLoadingRevision,
    revisionLoadError,
    selectRevision,
    clearRevisionLoadError,
  } = useWorkflowStudioRevisionSelection(
    activeRevision,
    originalYaml,
    draftYaml,
    setViewState,
    fetchRevisionDetail
  )

  useDraftBaselineSync(
    originalYaml,
    activeRevision?.id,
    draftYaml,
    setDraftYaml,
    setViewState,
    clearRevisionLoadError,
    viewState.hasPreservedDraft
  )

  const readOnly = isRevisionReadOnly(viewState)
  const definitionYaml =
    viewState.mode === 'revision'
      ? (viewState.viewedRevision?.definitionYaml ?? '')
      : draftYaml
  // 草稿模式画布渲染草稿：解析成功即用草稿记录；编辑中途 YAML 非法时回退
  // 已发布 workflow（画布另有低噪提示），revision 模式渲染被查看版本不变。
  const draftWorkflow = useMemo(
    () => workflowYamlToDefinitionRecord(draftYaml),
    [draftYaml]
  )
  const visibleWorkflow =
    viewState.mode === 'revision'
      ? (viewState.viewedRevision?.workflow ?? activeWorkflow)
      : (draftWorkflow ?? activeWorkflow)
  const visibleRevision =
    viewState.mode === 'revision'
      ? (viewState.viewedRevision?.revision ?? activeRevision)
      : activeRevision

  const draftDirty = isDefinitionDirty(originalYaml, draftYaml)
  const dirty = viewState.mode === 'draft' && draftDirty
  const canSubmit = Boolean(
    workspaceId && draftYaml.trim() && dirty && !readOnly
  )

  function backToDraft() {
    clearRevisionLoadError()
    setViewState(createDraftViewState(activeRevision?.id ?? null))
  }

  function useViewedRevisionAsDraft() {
    if (!viewState.viewedRevision) return
    setDraftYaml(viewState.viewedRevision.definitionYaml)
    clearRevisionLoadError()
    setViewState(createDraftViewState(activeRevision?.id ?? null))
  }

  return {
    draftYaml,
    setDraftYaml,
    definitionYaml,
    visibleWorkflow,
    visibleRevision,
    readOnly,
    dirty,
    canSubmit,
    viewMode: viewState.mode,
    selectedRevisionId: viewState.selectedRevisionId,
    hasPreservedDraft: viewState.hasPreservedDraft,
    isLoadingRevision,
    revisionLoadError,
    selectRevision,
    backToDraft,
    useViewedRevisionAsDraft,
  }
}
