import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchJobDetail, deleteJob } from '../../api'
import { rerunJob, runToJob } from '../../api/jobApi'
import { useUiStore } from '../../stores/uiStore'
import { invalidateAgentWorkers } from '../../lib/agentWorkersInvalidation'
import { queryKeys } from '../../lib/queryKeys'
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
  const queryClient = useQueryClient()
  const { setPageTitle, setPageSubtitle } = useUiStore()
  const [actionError, setActionError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)

  const detailQuery = useQuery({
    queryKey: queryKeys.jobDetail(jobId ?? ''),
    queryFn: () => fetchJobDetail(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      POLLING_STATUSES.has(query.state.data?.job.status ?? '') ? 5000 : false,
  })
  const detail = detailQuery.data ?? null
  const detailRefetch = detailQuery.refetch
  const queryError = detailQuery.error
    ? detailQuery.error instanceof Error
      ? detailQuery.error.message
      : String(detailQuery.error)
    : ''
  const error = actionError || queryError

  // 子 hook 契约：() => Promise<JobDetail | null>，action 成功后手动触发。
  const refreshDetail = useCallback(async (): Promise<JobDetail | null> => {
    if (!jobId) return null
    const result = await detailRefetch()
    if (result.data) setActionError('')
    return result.data ?? null
  }, [jobId, detailRefetch])

  useEffect(() => {
    // Refresh executor visibility once on job detail mount.
    invalidateAgentWorkers(queryClient)
  }, [queryClient])

  useEffect(() => {
    if (!detail) return
    setPageTitle(detail.job.title || detail.job.source_id || '任务详情')
    setPageSubtitle(pageSubtitle(detail.job))
  }, [detail, setPageTitle, setPageSubtitle])

  useEffect(() => {
    return () => {
      setPageTitle(null)
      setPageSubtitle(null)
    }
  }, [jobId, setPageTitle, setPageSubtitle])

  const handleRerun = useCallback(
    async (nodeKey: string | null, fromFailedNode?: boolean) => {
      if (!jobId || !nodeKey || fromFailedNode) return
      setActionLoading(true)
      try {
        await rerunJob(jobId, nodeKey)
        await refreshDetail()
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err))
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
        setActionError(err instanceof Error ? err.message : String(err))
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
    setActionError
  )

  const handleUpgradeWorkflow = useUpgradeWorkflowAction(
    jobId,
    refreshDetail,
    setActionLoading,
    setActionError
  )

  const handlePackage = usePackageAction(
    workspaceId,
    jobId,
    refreshDetail,
    setActionLoading,
    setActionError
  )

  const handleClearPacked = useClearPackedAction(
    workspaceId,
    jobId,
    refreshDetail,
    setActionLoading,
    setActionError
  )

  const handleDelete = useCallback(async () => {
    if (!jobId || !workspaceId) return
    setActionLoading(true)
    try {
      await deleteJob(jobId)
      navigate(`/workspaces/${encodeURIComponent(workspaceId)}`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
      setActionLoading(false)
    }
  }, [jobId, workspaceId, navigate])

  return {
    detail,
    error,
    setError: setActionError,
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
