import { useCallback } from 'react'
import { upgradeJobWorkflow } from '../../api/jobWorkflowUpgradeApi'
import type { JobDetail } from '../../types/jobTypes'

type RefreshDetail = () => Promise<JobDetail | null>

export function useUpgradeWorkflowAction(
  jobId: string | undefined,
  refreshDetail: RefreshDetail,
  setActionLoading: (loading: boolean) => void,
  setError: (message: string) => void
) {
  return useCallback(async () => {
    if (!jobId) return
    setActionLoading(true)
    try {
      await upgradeJobWorkflow(jobId)
      await refreshDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActionLoading(false)
    }
  }, [jobId, refreshDetail, setActionLoading, setError])
}
