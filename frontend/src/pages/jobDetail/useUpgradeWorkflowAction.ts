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
        // The upgrade may have committed server-side while the response was
        // lost (e.g. a post-commit failure turned into a 500) — refresh the
        // authoritative detail state before surfacing the error so the UI
        // never shows stale pre-upgrade data. A failed refresh must not mask
        // the original error.
        await refreshDetail().catch(() => null)
        setError(err instanceof Error ? err.message : String(err))
        throw err
      } finally {
        setActionLoading(false)
      }
    },
    [jobId, refreshDetail, setActionLoading, setError]
  )
}
