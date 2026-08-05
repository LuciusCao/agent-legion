import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
  continueJob as apiContinueJob,
} from '../../../api/jobApi'
import { useUiStore } from '../../uiStore'
import { applyPatchToAccumulator } from '../filterLogic/optionAccumulator'
import {
  applyDeleteToFilteredIds,
  applyDeleteToFilterCounts,
} from '../filterLogic/incrementalFilters'
import {
  applyMutationResults,
  clearSucceededSelection,
} from './mutationResults'
import { countMutationResults, makeMutationToast } from '../mutationHelpers'
import {
  refreshAfterBatchOperation,
  resolveBatchTarget,
  resolveOpTarget,
} from './selectionModeState'
import type { JobState, JobStoreSet } from '../state'

export function deleteSucceededJobs(
  state: JobState,
  succeededIds: Set<string>
): Partial<JobState> {
  const nextSelected = new Set(state.selectedIds)
  const jobsById = { ...state.jobsById }
  const jobIds = state.jobIds.filter((id) => {
    if (!succeededIds.has(id)) return true
    nextSelected.delete(id)
    delete jobsById[id]
    return false
  })
  const jobs = jobIds.map((id) => jobsById[id]).filter(Boolean)
  applyPatchToAccumulator(
    state.optionAccumulator,
    state.jobsById,
    [],
    [...succeededIds]
  )
  return {
    jobs,
    jobsById,
    jobIds,
    jobIndexById: Object.fromEntries(jobs.map((job, index) => [job.id, index])),
    filteredJobIds: applyDeleteToFilteredIds(state.filteredJobIds, [
      ...succeededIds,
    ]),
    filterCounts: applyDeleteToFilterCounts(
      state.filterCounts,
      state.jobsById,
      [...succeededIds],
      state.filterConfig
    ),
    selectedIds: nextSelected,
    selectMode: nextSelected.size === 0 ? false : state.selectMode,
  }
}

export function batchActions(set: JobStoreSet, get: () => JobState) {
  return {
    async batchRerun(
      workspaceId: string,
      nodeKey: string | null,
      fromFailedNode?: boolean,
      jobIds?: string[]
    ) {
      const target = resolveOpTarget(get(), jobIds)
      if (!target) return { results: [] }
      set({ batchRerunLoading: true })
      try {
        const data = await batchRerunJobs(workspaceId, nodeKey, target, {
          fromFailedNode,
        })
        const results = data.results ?? []
        applyMutationResults(set, results, '重跑')
        await refreshAfterBatchOperation(get, workspaceId)
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
      const target = resolveBatchTarget(get())
      if (!target) return { results: [] }
      set({ batchDeleteLoading: true })
      try {
        const data = await batchDeleteJobs(workspaceId, target)
        const results = data.results ?? []
        const succeededIds = new Set(
          results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
        )
        set((state) => deleteSucceededJobs(state, succeededIds))
        const counts = countMutationResults(results)
        useUiStore
          .getState()
          .showToast(
            makeMutationToast('删除', counts),
            counts.failed > 0 ? 'error' : 'success'
          )
        await refreshAfterBatchOperation(get, workspaceId)
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
      const target = resolveBatchTarget(get())
      if (!target) return { results: [], succeeded_count: 0, failed_count: 0 }
      set({ batchPackageLoading: true })
      try {
        const data = await packageJobs(workspaceId, target)
        const results = data.results ?? []
        clearSucceededSelection(set, results)
        useUiStore
          .getState()
          .showToast(
            `打包完成：成功 ${data.succeeded_count} 项，失败 ${data.failed_count} 项`,
            data.failed_count > 0 ? 'error' : 'success'
          )
        await refreshAfterBatchOperation(get, workspaceId)
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
      const target = resolveBatchTarget(get())
      if (!target) return { results: [] }
      set({ batchRunToLoading: true })
      try {
        const data = await batchRunToJobs(
          workspaceId,
          targetNodeKey,
          target,
          startNodeKey
        )
        const results = data.results ?? []
        applyMutationResults(set, results, '运行到')
        await refreshAfterBatchOperation(get, workspaceId)
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

    async continueJob(jobId: string) {
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
  }
}
