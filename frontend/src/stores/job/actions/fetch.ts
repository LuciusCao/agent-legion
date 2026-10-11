import type { JobSummary } from '../../../types/jobTypes'
import { computeFilterCounts } from '../filterLogic/incrementalFilters'
import { createOptionAccumulator } from '../filterLogic/optionAccumulator'
import type { JobState, JobStoreSet } from '../state'
import { clearedSelectionState } from './selectionModeState'
import { filtersForWorkspace } from './workspaceFilterState'

export const normalizeJobs = (jobs: JobSummary[]) => ({
  jobs,
  jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
  jobIds: jobs.map((job) => job.id),
  jobIndexById: Object.fromEntries(jobs.map((job, index) => [job.id, index])),
  optionAccumulator: createOptionAccumulator(jobs),
})

export function isCurrentWorkspace(
  state: JobState,
  workspaceId: string
): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobIds.length > 0 &&
      state.jobIds.every(
        (id) => state.jobsById[id]?.workspace_id === workspaceId
      ))
  )
}

export function resetJobListForFilterChange(
  state: JobState
): Partial<JobState> {
  return {
    jobs: [],
    jobsById: {},
    jobIds: [],
    jobIndexById: {},
    filteredJobIds: [],
    filterCounts: computeFilterCounts([], {}, state.filterConfig),
    facets: null,
    nextCursor: null,
    hasMore: false,
    totalJobs: null,
    loadingMore: false,
    isLoading: true,
    listLoadError: null,
    // 列表基线作废即在途缓冲作废：snapshotInFlight 由 refreshFirstPage 在
    // 调用本函数后显式重新置位，其余调用方（resetForWorkspace）直接清空、
    // 跨 workspace 不残留。
    snapshotInFlight: false,
    pendingPatchBuffer: [],
  }
}

export const failJobFetch =
  (ws: string, msg: string) =>
  (state: JobState): Partial<JobState> =>
    state.jobsWorkspaceId === ws
      ? {
          listLoadError: msg,
          isLoading: false,
          jobs: [],
          jobsById: {},
          jobIds: [],
          jobIndexById: {},
          optionAccumulator: state.optionAccumulator,
          filteredJobIds: [],
          filterCounts: computeFilterCounts([], {}, state.filterConfig),
        }
      : {}

export const resetForWorkspace =
  (ws: string) =>
  (state: JobState): Partial<JobState> => {
    const keep = isCurrentWorkspace(state, ws)
    const filterConfig = filtersForWorkspace(state, ws)
    return {
      ...resetJobListForFilterChange({ ...state, filterConfig }),
      // #1183：revision 归零——revision 是跨 workspace 单调计数器语义，
      // 切换 workspace 后新库的快照从 0 重新比较；不重置时上一个
      // workspace 残留的高 revision 会把新 workspace 的合法快照全部
      // 丢弃（setJobsSnapshotUpdate 的 revision 守卫），页面卡 skeleton。
      revision: 0,
      jobsWorkspaceId: ws,
      ...(keep ? { selectedIds: state.selectedIds } : clearedSelectionState()),
      filterConfig,
    }
  }

export function fetchActions(set: JobStoreSet) {
  return {
    resetForWorkspace: (workspaceId: string) =>
      set(resetForWorkspace(workspaceId)),
    failJobFetch: (workspaceId: string, message: string) =>
      set(failJobFetch(workspaceId, message)),
  }
}
