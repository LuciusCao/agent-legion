import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchJobDetail, deleteJob } from '../../api'
import { rerunJob, runToJob } from '../../api/jobApi'
import { usePageHeaderStore } from '../../stores/pageHeaderStore'
import { useExecutorsStore } from '../../stores/executorsStore'
import type { JobDetail } from '../../types/jobTypes'
import { useContinueJobAction } from './useContinueJobAction'
import { pageSubtitle } from './jobDetailTitle'
import { POLLING_STATUSES } from './jobNodeHelpers'
import { useClearPackedAction, usePackageAction } from './usePackageAction'
import { useUpgradeWorkflowAction } from './useUpgradeWorkflowAction'

export function useJobDetail(
  workspaceId: string | undefined,
  jobId: string | undefined
) {
  const navigate = useNavigate()
  const { setPageTitle, setPageSubtitle } = usePageHeaderStore()
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const detailRef = useRef(detail)

  useEffect(() => {
    detailRef.current = detail
  }, [detail])

  const refreshDetail = useCallback(
    async (options?: { signal?: AbortSignal }): Promise<JobDetail | null> => {
      if (!jobId) return null
      try {
        const data = await fetchJobDetail(jobId)
        if (options?.signal?.aborted) return null
        setDetail(data)
        setError('')
        return data
      } catch (err) {
        if (options?.signal?.aborted) return null
        setError(err instanceof Error ? err.message : String(err))
        return null
      }
    },
    [jobId]
  )

  useEffect(() => {
    // Refresh executor visibility once on job detail mount.
    void useExecutorsStore.getState().refreshWorkers()
  }, [])

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      const status = detailRef.current?.job.status
      if (status && POLLING_STATUSES.has(status)) {
        void refreshDetail({ signal: controller.signal })
      }
    }, 5000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [jobId, refreshDetail])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null)
    setError('')
    if (!jobId) return
    const controller = new AbortController()
    refreshDetail({ signal: controller.signal }).then((data) => {
      if (controller.signal.aborted || !data) return
      setPageTitle(data.job.title || data.job.source_id || '任务详情')
      setPageSubtitle(pageSubtitle(data.job))
    })
    return () => {
      controller.abort()
      setPageTitle(null)
      setPageSubtitle(null)
    }
  }, [jobId, setPageTitle, setPageSubtitle, refreshDetail])

  const handleRerun = useCallback(
    async (nodeKey: string | null, fromFailedNode?: boolean) => {
      if (!jobId || !nodeKey || fromFailedNode) return
      setActionLoading(true)
      try {
        await rerunJob(jobId, nodeKey)
        await refreshDetail()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setActionLoading(false)
      }
    },
    [jobId, refreshDetail]
  )

  const handleRunTo = useCallback(
    async (targetKey: string, startKey?: string) => {
      if (!jobId) return
      setActionLoading(true)
      try {
        await runToJob(jobId, targetKey, startKey)
        await refreshDetail()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setActionLoading(false)
      }
    },
    [jobId, refreshDetail]
  )

  const handleContinue = useContinueJobAction(
    jobId,
    refreshDetail,
    setActionLoading,
    setError
  )

  const handleUpgradeWorkflow = useUpgradeWorkflowAction(
    jobId,
    refreshDetail,
    setActionLoading,
    setError
  )

  const handlePackage = usePackageAction(
    workspaceId,
    jobId,
    refreshDetail,
    setActionLoading,
    setError
  )

  const handleClearPacked = useClearPackedAction(
    workspaceId,
    jobId,
    refreshDetail,
    setActionLoading,
    setError
  )

  const handleDelete = useCallback(async () => {
    if (!jobId || !workspaceId) return
    setActionLoading(true)
    try {
      await deleteJob(jobId)
      navigate(`/workspaces/${encodeURIComponent(workspaceId)}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionLoading(false)
    }
  }, [jobId, workspaceId, navigate])

  return {
    detail,
    error,
    setError,
    actionLoading,
    refreshDetail,
    handleRerun,
    handleRunTo,
    handleContinue,
    handleUpgradeWorkflow,
    handlePackage,
    handleClearPacked,
    handleDelete,
  }
}
