import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStats } from './useWorkspaceStats'
import type { FailureCategory } from '../types/failureTypes'
import type { FailureCategoryContext } from '../components/JobRerunDialog/useFailureCategories'

export function useWorkspaceRerunActions(workspaceId: string | undefined) {
  const batchRerun = useJobStore((state) => state.batchRerun)
  const rerunByFailure = useJobStore((state) => state.rerunByFailureCategory)
  const { data: workspaceStats } = useWorkspaceStats(workspaceId)

  // workflow_key 已 deprecated（#211 Phase 2）：与 workspace_id 恒等，
  // 失败过滤上下文改读 workspace_id。
  const workflowKey = workspaceStats?.workspace_id
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
