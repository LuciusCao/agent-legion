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
   * 任务列表加载失败的唯一信号（#1183）：只有整页快照/首屏加载的失败臂
   * （failJobFetch、refreshFirstPage catch）可写入，成功快照/筛选重试/
   * workspace 重置清除。批量与单项 mutation 的错误只走 toast 呈现、不落
   * store——共享 error 通道被 mutation 复用时，一次批量操作失败即把健康
   * 列表整页替换成错误页并冻结 SSE patch（PR #1189 评审 P1），新写入方
   * 一律走 toast，不得复用本字段。
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
