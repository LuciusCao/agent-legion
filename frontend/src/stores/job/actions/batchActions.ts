import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
  continueJob as apiContinueJob,
} from '../../../jobApi'
import { upgradeJobWorkflow } from '../../../jobWorkflowUpgradeApi'
import type { JobMutationResult } from '../../../jobTypes'
import { useUiStore } from '../../uiStore'
import {
  countMutationResults,
  makeMutationToast,
  type JobState,
  type JobStoreSet,
} from '../state'

export function batchActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchRerun(
      workspaceId: string,
      nodeKey: string | null,
      fromFailedNode?: boolean
    ) {
      const ids = Array.from(get().selectedIds)
      if (ids.length === 0) return { results: [] }
      set({ batchRerunLoading: true })
      try {
        const data = await batchRerunJobs(workspaceId, nodeKey, ids, {
          fromFailedNode,
        })
        const results = data.results ?? []
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => {
          const nextSelected = new Set(state.selectedIds)
          for (const id of succeededIds) {
            nextSelected.delete(id)
          }
          return {
            selectedIds: nextSelected,
            selectMode: nextSelected.size === 0 ? false : state.selectMode,
          }
        })
        const counts = countMutationResults(results)
        useUiStore
          .getState()
          .showToast(
            makeMutationToast('重跑', counts),
            counts.failed > 0 ? 'error' : 'success'
          )
        await get().fetchJobs(workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch rerun failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchRerunLoading: false })
      }
    },
    async batchDelete(workspaceId: string) {
      const ids = Array.from(get().selectedIds)
      if (ids.length === 0) return { results: [] }
      set({ batchDeleteLoading: true })
      try {
        const data = await batchDeleteJobs(workspaceId, ids)
        const results = data.results ?? []
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => {
          const nextSelected = new Set(state.selectedIds)
          const nextJobs = state.jobs.filter((j) => {
            if (succeededIds.has(j.id)) {
              nextSelected.delete(j.id)
              return false
            }
            return true
          })
          return {
            jobs: nextJobs,
            selectedIds: nextSelected,
            selectMode: nextSelected.size === 0 ? false : state.selectMode,
          }
        })
        const counts = countMutationResults(results)
        useUiStore
          .getState()
          .showToast(
            makeMutationToast('删除', counts),
            counts.failed > 0 ? 'error' : 'success'
          )
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch delete failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchDeleteLoading: false })
      }
    },

    async batchPackage(workspaceId: string) {
      const ids = Array.from(get().selectedIds)
      if (ids.length === 0)
        return { results: [], succeeded_count: 0, failed_count: 0 }
      set({ batchPackageLoading: true })
      try {
        const data = await packageJobs(workspaceId, ids)
        const results = data.results ?? []
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => {
          const nextSelected = new Set(state.selectedIds)
          for (const id of succeededIds) {
            nextSelected.delete(id)
          }
          return {
            selectedIds: nextSelected,
            selectMode: nextSelected.size === 0 ? false : state.selectMode,
          }
        })
        useUiStore
          .getState()
          .showToast(
            `打包完成：成功 ${data.succeeded_count} 项，失败 ${data.failed_count} 项`,
            data.failed_count > 0 ? 'error' : 'success'
          )
        await get().fetchJobs(workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch package failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchPackageLoading: false })
      }
    },

    async batchRunTo(
      workspaceId: string,
      targetNodeKey: string,
      startNodeKey?: string
    ) {
      const ids = Array.from(get().selectedIds)
      if (ids.length === 0) return { results: [] }
      set({ batchRunToLoading: true })
      try {
        const data = await batchRunToJobs(
          workspaceId,
          targetNodeKey,
          ids,
          startNodeKey
        )
        const results = data.results ?? []
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => {
          const nextSelected = new Set(state.selectedIds)
          for (const id of succeededIds) {
            nextSelected.delete(id)
          }
          return {
            selectedIds: nextSelected,
            selectMode: nextSelected.size === 0 ? false : state.selectMode,
          }
        })
        const counts = countMutationResults(results)
        useUiStore
          .getState()
          .showToast(
            makeMutationToast('运行到', counts),
            counts.failed > 0 ? 'error' : 'success'
          )
        await get().fetchJobs(workspaceId)
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch run-to failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchRunToLoading: false })
      }
    },

    async continueJob(jobId: string): Promise<JobMutationResult> {
      set({ continueLoading: true })
      try {
        const data = await apiContinueJob(jobId)
        useUiStore
          .getState()
          .showToast(
            data.status === 'succeeded'
              ? '继续完整流程成功'
              : '继续完整流程失败',
            data.status === 'succeeded' ? 'success' : 'error'
          )
        return data
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Continue full flow failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ continueLoading: false })
      }
    },

    async batchUpgradeWorkflow(workspaceId: string, jobIds: string[]) {
      if (jobIds.length === 0) return { results: [] }
      set({ batchUpgradeWorkflowLoading: true })
      const results: JobMutationResult[] = []
      try {
        for (const jobId of jobIds) {
          try {
            const result = await upgradeJobWorkflow(jobId)
            results.push(result)
          } catch (err) {
            results.push({
              job_id: jobId,
              operation: 'upgrade_workflow',
              status: 'failed',
              message: err instanceof Error ? err.message : String(err),
            })
          }
        }
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => {
          const nextSelected = new Set(state.selectedIds)
          for (const id of succeededIds) {
            nextSelected.delete(id)
          }
          return {
            selectedIds: nextSelected,
            selectMode: nextSelected.size === 0 ? false : state.selectMode,
          }
        })
        const counts = countMutationResults(
          results as { status: 'succeeded' | 'skipped' | 'failed' }[]
        )
        useUiStore
          .getState()
          .showToast(
            makeMutationToast('升级 workflow', counts),
            counts.failed > 0 ? 'error' : 'success'
          )
        await get().fetchJobs(workspaceId)
        return { results }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Batch workflow upgrade failed'
        set({ error: message })
        useUiStore.getState().showToast(message, 'error')
        throw err
      } finally {
        set({ batchUpgradeWorkflowLoading: false })
      }
    },
  }
}
