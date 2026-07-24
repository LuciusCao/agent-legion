import { useCallback } from 'react'
import { clearJobsPackedStatus, packageJobs } from '../../api/jobApi'
import type { JobDetail } from '../../types/jobTypes'

type RefreshDetail = () => Promise<JobDetail | null>

type SetActionLoading = (loading: boolean) => void
type SetError = (message: string) => void

export function usePackageAction(
  workspaceId: string | undefined,
  jobId: string | undefined,
  refreshDetail: RefreshDetail,
  setActionLoading: SetActionLoading,
  setError: SetError
) {
  return useCallback(async () => {
    if (!workspaceId || !jobId) return
    setActionLoading(true)
    try {
      const result = await packageJobs(workspaceId, [jobId])
      if (result.download_url) {
        window.open(result.download_url, '_blank')
      }
      await refreshDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActionLoading(false)
    }
  }, [workspaceId, jobId, refreshDetail, setActionLoading, setError])
}

export function useClearPackedAction(
  workspaceId: string | undefined,
  jobId: string | undefined,
  refreshDetail: RefreshDetail,
  setActionLoading: SetActionLoading,
  setError: SetError
) {
  return useCallback(async () => {
    if (!workspaceId || !jobId) return
    setActionLoading(true)
    try {
      await clearJobsPackedStatus(workspaceId, [jobId])
      await refreshDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActionLoading(false)
    }
  }, [workspaceId, jobId, refreshDetail, setActionLoading, setError])
}
