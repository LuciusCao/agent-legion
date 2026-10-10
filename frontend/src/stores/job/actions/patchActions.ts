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
  // listLoadError: null 会让「加载失败→假空白」重新满足引导页判定，
  // #1183 症状复发。丢弃 patch；listLoadError 只由整页快照成功、筛选重试
  // 或 workspace 重置清除——恢复依赖整页快照成功落地（SSE 重连/open 重拉、
  // 筛选变更触发重试、错误页「重试」按钮）；快照端点持续失败时不会有后续
  // 快照，被丢弃 patch 期间的更新随下一次成功快照整体重建。
  if (
    state.jobsWorkspaceId !== workspaceId ||
    revision <= state.revision ||
    state.listLoadError !== null
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
    listLoadError: null,
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
