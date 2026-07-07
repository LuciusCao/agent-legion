import type {
  BatchJobMutationResult,
  JobSummary,
  WorkspacePackageResult,
} from '../../jobTypes'
export {
  countMutationResults,
  makeMutationToast,
  normalizeJobStatus,
  type MutationCounts,
} from './mutationHelpers'
export type JobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'paused'
export interface JobFilterConfig {
  status: JobStatus | null
  search: string
  workflowVersion: number | 'none' | null
  activeNodeKey: string | null
}
export interface JobState {
  jobs: JobSummary[]
  jobsById: Record<string, JobSummary>
  jobIds: string[]
  revision: number
  jobsWorkspaceId: string | null
  isLoading: boolean
  error: string | null
  selectedIds: Set<string>
  expandedId: string | null
  filterConfig: JobFilterConfig
  selectMode: boolean
  batchDeleteLoading: boolean
  batchPackageLoading: boolean
  batchRerunLoading: boolean
  batchRunToLoading: boolean
  continueLoading: boolean
  batchUpgradeWorkflowLoading: boolean
  fetchJobs: (workspaceId: string) => Promise<void>
  resetForWorkspace: (workspaceId: string) => void
  setJobsAndFinishLoading: (jobs: JobSummary[]) => void
  failJobFetch: (workspaceId: string, message: string) => void
  setJobsSnapshot: (
    workspaceId: string,
    revision: number,
    jobs: JobSummary[]
  ) => void
  applyJobPatchBatch: (
    workspaceId: string,
    revision: number,
    jobs: JobSummary[],
    deletedJobIds: string[]
  ) => void
  setFilterConfig: (config: Partial<JobFilterConfig>) => void
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
    fromFailedNode?: boolean
  ) => Promise<BatchJobMutationResult>
  batchDelete: (workspaceId: string) => Promise<BatchJobMutationResult>
  batchPackage: (workspaceId: string) => Promise<WorkspacePackageResult>
  batchRunTo: (
    workspaceId: string,
    targetNodeKey: string,
    startNodeKey?: string
  ) => Promise<BatchJobMutationResult>
  continueJob: (jobId: string) => Promise<{
    job_id: string
    operation: string
    status: string
    message?: string | null
    node_key?: string | null
    reason_code?: string | null
  }>
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
