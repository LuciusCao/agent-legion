import type {
  BatchJobMutationResult,
  JobSummary,
  UpgradeMode,
  WorkspacePackageResult,
} from '../../types/jobTypes'
export type { UpgradeMode } from '../../types/jobTypes'
import type { ClearPackedActions } from './actions/clearPackedActions'
import type { ContinueJobResult, RerunByFailureActions } from './stateTypes'
import type { JobPaginationState } from './paginationTypes'
import type { JobSelectionModeState } from './selectionModeTypes'
export {
  countMutationResults,
  makeMutationToast,
  normalizeJobStatus,
  type MutationCounts,
} from './mutationHelpers'
export type { JobFilterConfig, JobStatus } from './filterConfig'
export type { JobFilterOptionAccumulator } from './filterLogic/optionAccumulator'
export type { FilterCounts } from './filterLogic/types'
export interface JobState
  extends
    ClearPackedActions,
    RerunByFailureActions,
    JobPaginationState,
    JobSelectionModeState {
  jobs: JobSummary[]
  jobsById: Record<string, JobSummary>
  jobIds: string[]
  jobIndexById: Record<string, number>
  revision: number
  filteredJobIds: string[]
  filterCounts: import('./filterLogic/types').FilterCounts
  optionAccumulator: import('./filterLogic/optionAccumulator').JobFilterOptionAccumulator
  jobsWorkspaceId: string | null
  isLoading: boolean
  /**
   * refreshFirstPage 在途标记（#1189 codex P1-c）：置位期间到达的
   * job_patch_batch 进 pendingPatchBuffer、state.revision 冻结——快照以
   * 真实 revision 落地后，「应用快照 + 按序重放缓冲」在同一个 set 内原子
   * 完成，水位与内容恒一致。仅 refreshFirstPage 置位/复位；workspace
   * 重置与失败终态一并清理。
   */
  snapshotInFlight: boolean
  /**
   * snapshotInFlight 期间的 patch 缓冲（上限见 patchActions）；重放时
   * revision ≤ 快照的由守卫幂等丢弃，> 的应用。
   */
  pendingPatchBuffer: Array<{
    revision: number
    jobs: JobSummary[]
    deletedJobIds: string[]
  }>
  /**
   * 任务列表加载失败的唯一信号（#1183）：只有整页快照/首屏加载的失败臂
   * （failJobFetch、refreshFirstPage catch）可写入；清除点为成功快照、
   * workspace 重置、筛选重试（refreshFirstPage 入口 reset）、
   * refreshFirstPage 重拉前的自愈臂（#1189 codex P1-d，
   * 同 set 成对恢复 isLoading 并重新武装 patch 缓冲）。批量与单项
   * mutation 的错误只走 toast 呈现、不落 store——共享 error 通道被
   * mutation 复用时，一次批量操作失败即把健康列表整页替换成错误页并
   * 冻结 SSE patch（PR #1189 评审 P1），新写入方一律走 toast，不得复用
   * 本字段。
   */
  listLoadError: string | null
  selectedIds: Set<string>
  expandedId: string | null
  filterConfig: import('./filterConfig').JobFilterConfig
  selectMode: boolean
  batchDeleteLoading: boolean
  batchPackageLoading: boolean
  batchClearPackedLoading: boolean
  batchRerunLoading: boolean
  batchRunToLoading: boolean
  batchPauseLoading: boolean
  batchResumeLoading: boolean
  continueLoading: boolean
  batchUpgradeWorkflowLoading: boolean
  resetForWorkspace: (workspaceId: string) => void
  failJobFetch: (workspaceId: string, message: string) => void
  setJobsSnapshot: (
    workspaceId: string,
    revision: number,
    jobs: JobSummary[]
  ) => void
  appendJobsSnapshot: (workspaceId: string, jobs: JobSummary[]) => void
  applyJobPatchBatch: (
    workspaceId: string,
    revision: number,
    jobs: JobSummary[],
    deletedJobIds: string[]
  ) => void
  setFilterConfig: (
    config: Partial<import('./filterConfig').JobFilterConfig>
  ) => void
  toggleSelectMode: () => void
  toggleSelect: (id: string) => void
  selectAll: () => void
  selectFailed: () => void
  selectUnpacked: () => void
  clearSelection: () => void
  toggleExpand: (id: string) => void
  getFilteredJobs: () => JobSummary[]
  batchRerun: (
    workspaceId: string,
    nodeKey: string | null,
    fromFailedNode?: boolean,
    jobIds?: string[]
  ) => Promise<BatchJobMutationResult>
  batchDelete: (workspaceId: string) => Promise<BatchJobMutationResult>
  batchPackage: (workspaceId: string) => Promise<WorkspacePackageResult>
  batchRunTo: (
    workspaceId: string,
    targetNodeKey: string,
    startNodeKey?: string
  ) => Promise<BatchJobMutationResult>
  batchPause: (
    workspaceId: string,
    reason?: string
  ) => Promise<BatchJobMutationResult>
  batchResume: (workspaceId: string) => Promise<BatchJobMutationResult>
  continueJob: (jobId: string) => ContinueJobResult
  batchUpgradeWorkflow: (
    workspaceId: string,
    jobIds?: string[],
    mode?: UpgradeMode
  ) => Promise<BatchJobMutationResult>
}
export type JobStoreSet = (
  partial:
    | JobState
    | Partial<JobState>
    | ((state: JobState) => JobState | Partial<JobState>),
  replace?: boolean
) => void
