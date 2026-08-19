import { useJobStore } from '../stores/jobStore'

/** Batch pause/resume handlers plus their combined loading flag. */
export function useWorkspacePauseActions(workspaceId: string | undefined) {
  const batchPause = useJobStore((state) => state.batchPause)
  const batchResume = useJobStore((state) => state.batchResume)
  const pauseLoading = useJobStore(
    (state) => state.batchPauseLoading || state.batchResumeLoading
  )

  const handlePause = async () => {
    if (workspaceId) await batchPause(workspaceId)
  }

  const handleResume = async () => {
    if (workspaceId) await batchResume(workspaceId)
  }

  return { handlePause, handleResume, pauseLoading }
}
