import { useCallback } from 'react'
import { upgradeJobWorkflow } from '../../api/jobWorkflowUpgradeApi'
import type { JobDetail, UpgradeMode } from '../../types/jobTypes'

type RefreshDetail = () => Promise<JobDetail | null>

export function useUpgradeWorkflowAction(
  jobId: string | undefined,
  refreshDetail: RefreshDetail,
  setActionLoading: (loading: boolean) => void,
  setError: (message: string) => void
) {
  return useCallback(
    async (mode: UpgradeMode = 'clean') => {
      if (!jobId) return
      setActionLoading(true)
      try {
        await upgradeJobWorkflow(jobId, mode)
        await refreshDetail()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        throw err
      } finally {
        setActionLoading(false)
      }
    },
    [jobId, refreshDetail, setActionLoading, setError]
  )
}
