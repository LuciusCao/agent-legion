import { useJobStore } from '../stores/jobStore'

/** Batch execution-control handlers plus their combined loading flag. */
export function useWorkspacePauseActions(workspaceId: string | undefined) {
  const batchPause = useJobStore((state) => state.batchPause)
  const batchResume = useJobStore((state) => state.batchResume)
  const batchApproval = useJobStore((state) => state.batchApproval)
  const pauseLoading = useJobStore(
    (state) =>
      state.batchPauseLoading ||
      state.batchResumeLoading ||
      state.batchApprovalLoading
  )

  const handlePause = async () => {
    if (workspaceId) await batchPause(workspaceId)
  }

  const handleResume = async () => {
    if (workspaceId) await batchResume(workspaceId)
  }

  const handleApproval = async (kind: 'approve' | 'reject') => {
    if (workspaceId) await batchApproval(workspaceId, kind)
  }

  return { handlePause, handleResume, handleApproval, pauseLoading }
}
