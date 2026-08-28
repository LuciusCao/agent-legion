import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../../types'

export type RevisionViewMode = 'draft' | 'revision'

export type ViewedRevision = {
  revision: WorkflowRevisionSummary
  workflow: WorkflowDefinitionRecord
  definitionYaml: string
}

export type WorkflowStudioViewState = {
  mode: RevisionViewMode
  selectedRevisionId: string | null
  draftBaseRevisionId: string | null
  viewedRevision: ViewedRevision | null
  hasPreservedDraft: boolean
}

export function isRevisionReadOnly(
  viewState: WorkflowStudioViewState
): boolean {
  return viewState.mode === 'revision'
}

export function createDraftViewState(
  activeRevisionId: string | null
): WorkflowStudioViewState {
  return {
    mode: 'draft',
    selectedRevisionId: activeRevisionId,
    draftBaseRevisionId: activeRevisionId,
    viewedRevision: null,
    hasPreservedDraft: false,
  }
}
