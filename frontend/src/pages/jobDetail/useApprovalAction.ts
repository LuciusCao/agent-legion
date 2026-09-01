import { useCallback } from 'react'
import { decideApproval, type ApprovalVerdict } from '../../api/jobApprovalApi'
import type { JobDetail } from '../../types/jobTypes'

/**
 * 审批决定动作（EXEC-APPROVAL-001）：与 useContinueJobAction 同构 —
 * 提交决定、刷新详情、错误落到页面级 error 并向上抛出（对话框据此保持打开）。
 */
export function useApprovalAction(
  workspaceId: string | undefined,
  jobId: string | undefined,
  refreshDetail: () => Promise<JobDetail | null>,
  setActionLoading: (loading: boolean) => void,
  setActionError: (error: string) => void
) {
  return useCallback(
    async (
      nodeKey: string,
      verdict: ApprovalVerdict,
      note: string,
      reworkTarget: string
    ) => {
      if (!jobId || !workspaceId) return
      setActionLoading(true)
      try {
        await decideApproval(workspaceId, jobId, nodeKey, {
          verdict,
          note,
          rework_target: reworkTarget,
        })
        await refreshDetail()
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err))
        throw err
      } finally {
        setActionLoading(false)
      }
    },
    [workspaceId, jobId, refreshDetail, setActionLoading, setActionError]
  )
}
