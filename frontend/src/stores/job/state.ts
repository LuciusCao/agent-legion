import type {
  BatchJobMutationResult,
  JobSummary,
  WorkspacePackageResult,
} from '../../jobTypes'

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface JobState {
  jobs: JobSummary[]
  isLoading: boolean
  error: string | null
  selectedIds: Set<string>
  expandedId: string | null
  statusFilter: JobStatus | 'all'
  searchQuery: string
  selectMode: boolean
  batchDeleteLoading: boolean
  batchPackageLoading: boolean
  batchRerunLoading: boolean
  batchRunToLoading: boolean
  continueLoading: boolean

  fetchJobs: (workspaceId: string) => Promise<void>
  setJobs: (jobs: JobSummary[]) => void
  setStatusFilter: (filter: JobStatus | 'all') => void
  setSearchQuery: (query: string) => void
  toggleSelectMode: () => void
  toggleSelect: (id: string) => void
  selectAll: () => void
  selectFailed: () => void
  clearSelection: () => void
  toggleExpand: (id: string) => void
  getFilteredJobs: () => JobSummary[]
  batchRerun: (
    workspaceId: string,
    nodeKey: string
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
}

export type JobStoreSet = (
  partial:
    | JobState
    | Partial<JobState>
    | ((state: JobState) => JobState | Partial<JobState>),
  replace?: boolean
) => void

export type MutationCounts = {
  succeeded: number
  skipped: number
  failed: number
}

export function countMutationResults(
  results: { status: 'succeeded' | 'skipped' | 'failed' }[]
): MutationCounts {
  return results.reduce(
    (acc, r) => {
      if (r.status === 'succeeded') acc.succeeded += 1
      else if (r.status === 'skipped') acc.skipped += 1
      else if (r.status === 'failed') acc.failed += 1
      return acc
    },
    { succeeded: 0, skipped: 0, failed: 0 }
  )
}

export function makeMutationToast(
  action: string,
  counts: MutationCounts
): string {
  if (counts.skipped === 0 && counts.failed === 0) {
    return `${action}完成：成功 ${counts.succeeded} 项`
  }
  if (counts.failed === 0) {
    return `${action}完成：成功 ${counts.succeeded} 项，跳过 ${counts.skipped} 项`
  }
  return `${action}完成：成功 ${counts.succeeded} 项，跳过 ${counts.skipped} 项，失败 ${counts.failed} 项`
}

export function normalizeJobStatus(status: string): JobStatus {
  switch (status) {
    case 'pending':
    case 'running':
    case 'completed':
    case 'failed':
      return status
    default:
      return 'pending'
  }
}
