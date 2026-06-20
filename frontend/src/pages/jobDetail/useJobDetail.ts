import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchJobDetail, deleteJob } from '../../api'
import { rerunJob, runToJob, packageJobs } from '../../jobApi'
import { useUiStore } from '../../stores/uiStore'
import { useJobStore } from '../../stores/jobStore'
import type { JobDetailResponse } from '../../types'
import { POLLING_STATUSES } from './jobNodeHelpers'

export function useJobDetail(
  workspaceId: string | undefined,
  jobId: string | undefined
) {
  const navigate = useNavigate()
  const { setPageTitle } = useUiStore()
  const [detail, setDetail] = useState<JobDetailResponse | null>(null)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const detailRef = useRef(detail)

  useEffect(() => {
    detailRef.current = detail
  }, [detail])

  const refreshDetail = useCallback(
    async (options?: {
      signal?: AbortSignal
    }): Promise<JobDetailResponse | null> => {
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

  // Preserve the page's existing polling behavior for active jobs.
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
    })
    return () => {
      controller.abort()
      setPageTitle(null)
    }
  }, [jobId, setPageTitle, refreshDetail])

  const handleRerun = useCallback(
    async (nodeKey: string) => {
      if (!jobId) return
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

  const handleContinue = useCallback(async () => {
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
  }, [jobId, refreshDetail])

  const handlePackage = useCallback(async () => {
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
  }, [workspaceId, jobId, refreshDetail])

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
    handlePackage,
    handleDelete,
  }
}
