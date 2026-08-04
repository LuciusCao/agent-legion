import { clearJobsPackedStatus } from '../../../api/jobApi'
import type { WorkspacePackageStatusResetResult } from '../../../types/jobTypes'
import { useUiStore } from '../../uiStore'
import {
  refreshAfterBatchOperation,
  resolveBatchTarget,
} from './selectionModeState'
import type { JobState, JobStoreSet } from '../state'

export type ClearPackedActions = {
  batchClearPacked: (
    workspaceId: string
  ) => Promise<WorkspacePackageStatusResetResult>
}

export function clearPackedActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchClearPacked(workspaceId: string) {
      const target = resolveBatchTarget(get())
      if (!target) return { results: [], succeeded_count: 0, failed_count: 0 }
      set({ batchClearPackedLoading: true })
      try {
        const data = await clearJobsPackedStatus(workspaceId, target)
        useUiStore
          .getState()
          .showToast(
            `已清空打包状态：成功 ${data.succeeded_count} 项，失败 ${data.failed_count} 项`,
            data.failed_count > 0 ? 'error' : 'success'
          )
        await refreshAfterBatchOperation(get, workspaceId)
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
