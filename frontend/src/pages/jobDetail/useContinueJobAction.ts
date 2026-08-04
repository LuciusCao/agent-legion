import { useCallback } from 'react'
import { useJobStore } from '../../stores/jobStore'
import type { JobDetail } from '../../types/jobTypes'

type RefreshDetail = () => Promise<JobDetail | null>

export function useContinueJobAction(
  jobId: string | undefined,
  refreshDetail: RefreshDetail,
  setActionLoading: (loading: boolean) => void,
  setError: (message: string) => void
) {
  return useCallback(async () => {
    if (!jobId) return
    setActionLoading(true)
    try {
      await useJobStore.getState().continueJob(jobId)
      await refreshDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActionLoading(false)
    }
  }, [jobId, refreshDetail, setActionLoading, setError])
}
