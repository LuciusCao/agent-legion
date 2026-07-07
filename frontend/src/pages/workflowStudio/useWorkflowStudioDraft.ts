import { useEffect, useState } from 'react'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../types'
import { isDefinitionDirty } from './workflowStudioModel'
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

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset draft to loaded original when it changes
    setDraftYaml(originalYaml)
    setViewState(createDraftViewState(activeRevision?.id ?? null))
  }, [originalYaml, activeRevision?.id])

  const readOnly = isRevisionReadOnly(viewState)
  const definitionYaml =
    viewState.mode === 'revision'
      ? (viewState.viewedRevision?.definitionYaml ?? '')
      : draftYaml
  const visibleWorkflow =
    viewState.mode === 'revision'
      ? (viewState.viewedRevision?.workflow ?? activeWorkflow)
      : activeWorkflow
  const visibleRevision =
    viewState.mode === 'revision'
      ? (viewState.viewedRevision?.revision ?? activeRevision)
      : activeRevision

  const dirty =
    viewState.mode === 'draft' && isDefinitionDirty(originalYaml, draftYaml)
  const canSubmit = Boolean(
    workspaceId && draftYaml.trim() && dirty && !readOnly
  )

  async function selectRevision(revisionId: string) {
    if (revisionId === activeRevision?.id) {
      setViewState((current) => ({
        ...createDraftViewState(activeRevision?.id ?? null),
        hasPreservedDraft: current.hasPreservedDraft,
      }))
      return
    }
    const detail = await fetchRevisionDetail(revisionId)
    setViewState({
      mode: 'revision',
      selectedRevisionId: detail.revision.id,
      draftBaseRevisionId: activeRevision?.id ?? null,
      viewedRevision: {
        revision: detail.revision,
        workflow: detail.workflow,
        definitionYaml: detail.definition_yaml,
      },
      hasPreservedDraft: isDefinitionDirty(originalYaml, draftYaml),
    })
  }

  function backToDraft() {
    setViewState(createDraftViewState(activeRevision?.id ?? null))
  }

  function useViewedRevisionAsDraft() {
    if (!viewState.viewedRevision) return
    setDraftYaml(viewState.viewedRevision.definitionYaml)
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
    selectRevision,
    backToDraft,
    useViewedRevisionAsDraft,
  }
}
