import { useJobStore } from '../stores/jobStore'

export function useWorkspacePackageActions(workspaceId: string | undefined) {
  const batchPackage = useJobStore((state) => state.batchPackage)
  const batchClearPacked = useJobStore((state) => state.batchClearPacked)
  const batchUpgradeWorkflow = useJobStore(
    (state) => state.batchUpgradeWorkflow
  )

  const handlePackage = async () => {
    if (!workspaceId) return
    const result = await batchPackage(workspaceId)
    if (result.download_url) window.open(result.download_url, '_blank')
  }

  const handleClearPacked = async () => {
    if (!workspaceId) return
    await batchClearPacked(workspaceId)
  }

  const handleUpgradeWorkflow = async (jobIds?: string[]) => {
    if (!workspaceId) return
    await batchUpgradeWorkflow(workspaceId, jobIds)
  }

  return { handlePackage, handleClearPacked, handleUpgradeWorkflow }
}
