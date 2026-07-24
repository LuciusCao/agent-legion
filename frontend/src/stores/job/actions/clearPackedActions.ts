import { clearJobsPackedStatus } from '../../../api/jobApi'
import type { WorkspacePackageStatusResetResult } from '../../../types/jobTypes'
import { useUiStore } from '../../uiStore'
import type { JobState, JobStoreSet } from '../state'

export type ClearPackedActions = {
  batchClearPacked: (
    workspaceId: string
  ) => Promise<WorkspacePackageStatusResetResult>
}

export function clearPackedActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchClearPacked(workspaceId: string) {
      const ids = Array.from(get().selectedIds)
      if (ids.length === 0)
        return { results: [], succeeded_count: 0, failed_count: 0 }
      set({ batchClearPackedLoading: true })
      try {
        const data = await clearJobsPackedStatus(workspaceId, ids)
        useUiStore
          .getState()
          .showToast(
            `已清空打包状态：成功 ${data.succeeded_count} 项，失败 ${data.failed_count} 项`,
            data.failed_count > 0 ? 'error' : 'success'
          )
        await get().fetchJobs(workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Clear packed status failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchClearPackedLoading: false })
      }
    },
  }
}
