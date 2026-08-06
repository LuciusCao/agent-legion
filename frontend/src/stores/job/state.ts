import type {
  BatchJobMutationResult,
  JobSummary,
  WorkspacePackageResult,
} from '../../types/jobTypes'
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
  error: string | null
  selectedIds: Set<string>
  expandedId: string | null
  filterConfig: import('./filterConfig').JobFilterConfig
  selectMode: boolean
  batchDeleteLoading: boolean
  batchPackageLoading: boolean
  batchClearPackedLoading: boolean
  batchRerunLoading: boolean
  batchRunToLoading: boolean
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
  continueJob: (jobId: string) => ContinueJobResult
  batchUpgradeWorkflow: (
    workspaceId: string,
    jobIds: string[]
  ) => Promise<BatchJobMutationResult>
}
export type JobStoreSet = (
  partial:
    | JobState
    | Partial<JobState>
    | ((state: JobState) => JobState | Partial<JobState>),
  replace?: boolean
) => void
