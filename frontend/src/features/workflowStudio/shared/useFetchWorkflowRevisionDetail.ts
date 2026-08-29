import { useCallback } from 'react'
import { fetchWorkflowRevisionDetail } from '../../../api'
import type { WorkflowRevisionDetailResponse } from '../../../types'

export function useFetchWorkflowRevisionDetail(
  workspaceId: string | undefined
) {
  return useCallback(
    async (revisionId: string): Promise<WorkflowRevisionDetailResponse> => {
      if (!workspaceId) {
        throw new Error('Workspace is required')
      }
      return fetchWorkflowRevisionDetail(workspaceId, revisionId)
    },
    [workspaceId]
  )
}
