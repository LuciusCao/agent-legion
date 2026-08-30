import { useCallback, useMemo, useState } from 'react'
import { JobApprovalDialog } from '../../components/job/JobApprovalDialog'
import type { ApprovalVerdict } from '../../api/jobApprovalApi'
import type { JobDetail } from '../../types/jobTypes'

// prettier-ignore
type DecideHandler = (nodeKey: string, verdict: ApprovalVerdict, note: string, reworkTarget: string) => Promise<void>

/**
 * 审批关卡的页面装配（EXEC-APPROVAL-001）：定位待审节点、管理对话框
 * 开关、把决定转发给 useApprovalAction，并返回可直接渲染的对话框元素。
 */
export function useJobApprovalGate(
  workspaceId: string | undefined,
  jobId: string | undefined,
  detail: JobDetail | null,
  actionLoading: boolean,
  onDecide: DecideHandler,
  onPreviewArtifact: (name: string) => void
) {
  const [approvalOpen, setApprovalOpen] = useState(false)

  // Render-phase reset on job switch (React's "adjusting state when props
  // change" pattern) — an effect-based reset would double-render every switch.
  const [lastJobId, setLastJobId] = useState(jobId)
  if (jobId !== lastJobId) {
    setLastJobId(jobId)
    setApprovalOpen(false)
  }

  const approvalGate = useMemo(
    () =>
      detail?.nodes.find((node) => node.status === 'awaiting_approval') ?? null,
    [detail]
  )

  const openApproval = useCallback(() => setApprovalOpen(true), [])

  const decideGate = useCallback(
    async (verdict: ApprovalVerdict, note: string, reworkTarget: string) => {
      if (!approvalGate) return
      try {
        await onDecide(approvalGate.node_key, verdict, note, reworkTarget)
        setApprovalOpen(false)
      } catch {
        // 错误已写入页面级 error 展示；对话框保持打开供重试。
      }
    },
    [approvalGate, onDecide]
  )

  const approvalDialog =
    detail && workspaceId && jobId && approvalGate ? (
      <JobApprovalDialog
        open={approvalOpen}
        workspaceId={workspaceId}
        jobId={jobId}
        gate={approvalGate}
        nodes={detail.nodes}
        loading={actionLoading}
        onPreviewArtifact={onPreviewArtifact}
        onDecide={decideGate}
        onClose={() => setApprovalOpen(false)}
      />
    ) : null

  return { openApproval, approvalDialog }
}
