import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import type { FailureCategory } from '../types/failureTypes'
import type { FailureCategoryContext } from '../components/JobRerunDialog/useFailureCategories'

export function useWorkspaceRerunActions(workspaceId: string | undefined) {
  const batchRerun = useJobStore((state) => state.batchRerun)
  const rerunByFailure = useJobStore((state) => state.rerunByFailureCategory)
  const workspaceStats = useWorkspaceStore((state) => state.workspaceStats)

  const workflowKey = workspaceId
    ? workspaceStats[workspaceId]?.workflow_key
    : undefined
  const failureContext: FailureCategoryContext | undefined = workspaceId
    ? { workspaceId, workflowKey }
    : undefined

  const handleRerun = async (
    nodeKey: string | null,
    fromFailedNode?: boolean,
    jobIds?: string[],
    failureCategory?: FailureCategory,
    fromNodeKey?: string
  ) => {
    if (!workspaceId) return
    if (failureCategory) {
      await rerunByFailure(workspaceId, {
        category: failureCategory,
        jobIds,
        fromNodeKey,
      })
      return
    }
    await batchRerun(workspaceId, nodeKey, fromFailedNode, jobIds)
  }

  return { handleRerun, failureContext }
}
