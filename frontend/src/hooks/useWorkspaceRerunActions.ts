import { useJobStore } from '../stores/jobStore'
import type { FailureCategory } from '../types/failureTypes'
import type { FailureCategoryContext } from '../components/JobRerunDialog/useFailureCategories'

export function useWorkspaceRerunActions(workspaceId: string | undefined) {
  const batchRerun = useJobStore((state) => state.batchRerun)
  const batchRunTo = useJobStore((state) => state.batchRunTo)
  const rerunByFailure = useJobStore((state) => state.rerunByFailureCategory)
  const failureContext: FailureCategoryContext | undefined = workspaceId
    ? { workspaceId }
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

  const handleRunTo = async (targetKey: string, startKey?: string) => {
    if (workspaceId) await batchRunTo(workspaceId, targetKey, startKey)
  }

  return { handleRerun, handleRunTo, failureContext }
}
