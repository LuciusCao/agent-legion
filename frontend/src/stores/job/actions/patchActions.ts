import type { JobSummary } from '../../../types/jobTypes'
import type { JobState, JobStoreSet } from '../state'
import { failJobFetch } from './fetch'
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
  // #1183 症状复发。丢弃 patch；listLoadError 的清除点：整页快照成功、
  // workspace 重置、筛选重试（refreshFirstPage 入口的 reset 覆盖）、
  // refreshFirstPage 重拉前的自愈臂（#1189 codex P1-d——清除时并无快照
  // 成功，但同 set 成对恢复 isLoading 并重新武装缓冲，语义是进入「重试
  // 在途」形态而非「恢复健康」）。恢复依赖整页快照成功落地（SSE 重连/
  // open 重拉、筛选变更触发重试、错误页「重试」按钮）；快照端点持续失败
  // 时不会有后续快照，被丢弃 patch 期间的更新随下一次成功快照整体重建。
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

// snapshotInFlight 期间的 patch 缓冲上限：溢出说明 refresh 在途遭遇 patch
// 风暴（每批可含数百任务），可靠收敛无望——清空缓冲并诚实走 failJobFetch
// （错误页 + 重试按钮），不提交半应用状态。
const MAX_PENDING_PATCH_BUFFER = 1000

export function patchActions(set: JobStoreSet) {
  return {
    applyJobPatchBatch: (
      workspaceId: string,
      revision: number,
      patchJobs: JobSummary[],
      deletedJobIds: string[]
    ) =>
      set((state) => {
        // #1189 codex P1-c：refreshFirstPage 在途期间 patch 进缓冲、
        // revision 冻结——快照以真实 revision 落地后由 refreshFirstPage 在
        // 同一 set 内按序重放（≤ 快照 revision 的被守卫幂等丢弃），水位与
        // 内容恒一致，不完整基线永不提交。
        if (state.snapshotInFlight && state.jobsWorkspaceId === workspaceId) {
          if (state.pendingPatchBuffer.length >= MAX_PENDING_PATCH_BUFFER) {
            return {
              ...failJobFetch(
                workspaceId,
                '任务更新过于频繁，列表未能收敛，请重试'
              )(state),
              snapshotInFlight: false,
              pendingPatchBuffer: [],
            }
          }
          return {
            pendingPatchBuffer: [
              ...state.pendingPatchBuffer,
              { revision, jobs: patchJobs, deletedJobIds },
            ],
          }
        }
        return (
          applyJobPatchBatchUpdate(
            state,
            workspaceId,
            revision,
            patchJobs,
            deletedJobIds
          ) ?? {}
        )
      }),
  }
}
