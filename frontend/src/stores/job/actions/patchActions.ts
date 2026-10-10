import type { JobSummary } from '../../../types/jobTypes'
import type { JobState, JobStoreSet } from '../state'
import { applyPatchToAccumulator } from '../filterLogic/optionAccumulator'
import { applyVisiblePatchJobs } from '../filterLogic/patchVisibility'
import {
  applyPatchToFilteredIds,
  applyPatchToFilterCounts,
} from '../filterLogic/incrementalFilters'

function buildPatchedCollections(
  state: JobState,
  jobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  deleted: Set<string>
) {
  const known = new Set(state.jobIds)
  for (const id of deleted) known.delete(id)
  const added = patchJobs
    .filter((job) => !known.has(job.id))
    .map((job) => job.id)
  const reordered = deleted.size > 0 || added.length > 0
  const jobIds = reordered
    ? [...added, ...state.jobIds.filter((id) => !deleted.has(id))]
    : state.jobIds
  const jobIndexById = reordered
    ? Object.fromEntries(jobIds.map((id, index) => [id, index]))
    : state.jobIndexById
  return {
    jobIds,
    jobIndexById,
    jobs: patchJobsArray(state, jobsById, patchJobs, reordered, jobIds),
  }
}

function patchJobsArray(
  state: JobState,
  jobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  reordered: boolean,
  jobIds: string[]
) {
  const jobs = reordered
    ? jobIds.map((id) => jobsById[id]).filter(Boolean)
    : state.jobs.slice()
  if (!reordered) {
    for (const job of patchJobs) {
      const index = state.jobIndexById[job.id]
      if (index !== undefined) jobs[index] = job
    }
  }
  return jobs
}

export function applyJobPatchBatchUpdate(
  state: JobState,
  workspaceId: string,
  revision: number,
  patchJobs: JobSummary[],
  deletedJobIds: string[]
): Partial<JobState> | null {
  // #1183：失败空态（failJobFetch / refreshFirstPage 失败终态）没有已加载
  // 基线——增量 patch 套在空列表上会拼出假的部分列表，且其无条件的
  // error: null 会让「加载失败→假空白」重新满足引导页判定，#1183 症状
  // 复发。丢弃 patch；error 只由整页快照成功、筛选重试或 workspace
  // 重置清除（被丢弃 patch 的 revision 由服务端单调的后续快照覆盖）。
  if (
    state.jobsWorkspaceId !== workspaceId ||
    revision <= state.revision ||
    state.error !== null
  )
    return null
  const deleted = new Set(deletedJobIds)
  const oldJobsById = state.jobsById
  const jobsById = { ...oldJobsById }
  for (const id of deleted) delete jobsById[id]
  const filterConfig = state.filterConfig
  const visibleJobs = applyVisiblePatchJobs(jobsById, patchJobs, filterConfig)
  const { jobs, jobIds, jobIndexById } = buildPatchedCollections(
    state,
    jobsById,
    visibleJobs,
    deleted
  )
  applyPatchToAccumulator(
    state.optionAccumulator,
    oldJobsById,
    visibleJobs,
    deletedJobIds
  )
  return {
    jobs,
    jobsById,
    jobIds,
    jobIndexById,
    filteredJobIds: applyPatchToFilteredIds(
      state.filteredJobIds,
      jobIndexById,
      oldJobsById,
      visibleJobs,
      deletedJobIds,
      filterConfig
    ),
    filterCounts: applyPatchToFilterCounts(
      state.filterCounts,
      oldJobsById,
      visibleJobs,
      deletedJobIds,
      filterConfig
    ),
    revision,
    isLoading: false,
    error: null,
  }
}

export function patchActions(set: JobStoreSet) {
  return {
    applyJobPatchBatch: (
      workspaceId: string,
      revision: number,
      patchJobs: JobSummary[],
      deletedJobIds: string[]
    ) =>
      set(
        (state) =>
          applyJobPatchBatchUpdate(
            state,
            workspaceId,
            revision,
            patchJobs,
            deletedJobIds
          ) ?? {}
      ),
  }
}
