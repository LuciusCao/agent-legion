import { useCallback, useState } from 'react'
import type {
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../types'
import { isDefinitionDirty } from './workflowStudioModel'
import {
  createDraftViewState,
  type WorkflowStudioViewState,
} from './workflowStudioViewState'

export function useWorkflowStudioRevisionSelection(
  activeRevision: WorkflowRevisionSummary | null,
  originalYaml: string,
  draftYaml: string,
  setViewState: (
    value:
      | WorkflowStudioViewState
      | ((prev: WorkflowStudioViewState) => WorkflowStudioViewState)
  ) => void,
  fetchRevisionDetail: (
    revisionId: string
  ) => Promise<WorkflowRevisionDetailResponse>
) {
  const [isLoadingRevision, setIsLoadingRevision] = useState(false)
  const [revisionLoadError, setRevisionLoadError] = useState<string | null>(
    null
  )

  const clearRevisionLoadError = useCallback(() => {
    setRevisionLoadError(null)
  }, [])

  async function selectRevision(revisionId: string) {
    clearRevisionLoadError()
    if (revisionId === activeRevision?.id) {
      setIsLoadingRevision(false)
      setViewState((current) => ({
        ...createDraftViewState(activeRevision?.id ?? null),
        hasPreservedDraft: current.hasPreservedDraft,
      }))
      return
    }
    setIsLoadingRevision(true)
    try {
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
    } catch (error) {
      setRevisionLoadError(
        error instanceof Error ? error.message : '加载版本详情失败'
      )
    } finally {
      setIsLoadingRevision(false)
    }
  }

  return {
    isLoadingRevision,
    revisionLoadError,
    selectRevision,
    clearRevisionLoadError,
  }
}
