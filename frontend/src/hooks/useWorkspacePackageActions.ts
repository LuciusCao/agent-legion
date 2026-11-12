import { useJobStore } from '../stores/jobStore'
import { isJobBatchAccepted } from '../api/jobBatchOperationsApi'
import type { UpgradeMode } from '../types/jobTypes'

export function useWorkspacePackageActions(workspaceId: string | undefined) {
  const batchPackage = useJobStore((state) => state.batchPackage)
  const batchClearPacked = useJobStore((state) => state.batchClearPacked)
  const batchUpgradeWorkflow = useJobStore(
    (state) => state.batchUpgradeWorkflow
  )

  const handlePackage = async () => {
    if (!workspaceId) return
    const result = await batchPackage(workspaceId)
    // 202 受理（#946）时 zip 尚未生成，下载入口在任务中心卡片上。
    if (isJobBatchAccepted(result)) return
    if (result.download_url) window.open(result.download_url, '_blank')
  }

  const handleClearPacked = async () => {
    if (!workspaceId) return
    await batchClearPacked(workspaceId)
  }

  const handleUpgradeWorkflow = async (
    jobIds?: string[],
    mode: UpgradeMode = 'clean'
  ) => {
    if (!workspaceId) return
    await batchUpgradeWorkflow(workspaceId, jobIds, mode)
  }

  return { handlePackage, handleClearPacked, handleUpgradeWorkflow }
}
