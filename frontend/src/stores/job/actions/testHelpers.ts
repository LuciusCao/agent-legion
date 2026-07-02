import type { JobState } from '../state'
import type {
  BatchJobMutationResult,
  JobSummary,
  WorkspacePackageResult,
} from '../../../jobTypes'

export function createJobSummary(
  partial: Partial<JobSummary> = {}
): JobSummary {
  return {
    active_node_key: null,
    batch_id: '',
    completed_nodes: 0,
    created_at: '',
    error_message: '',
    error_summary: '',
    execution_control: undefined,
    id: '',
    node_summaries: undefined,
    source_id: '',
    source_type: '',
    status: '',
    storage_dir: '',
    title: '',
    total_nodes: 0,
    updated_at: '',
    workflow_key: '',
    workspace_id: '',
    workflow_revision_id: '',
    workflow_definition_hash: '',
    outcome: '',
    current_workflow_revision_id: '',
    current_workflow_revision_version: null,
    ...partial,
  }
}
export function createJobState(partial: Partial<JobState> = {}): JobState {
  return {
    jobs: [],
    jobsWorkspaceId: null,
    isLoading: false,
    error: null,
    selectedIds: new Set(),
    expandedId: null,
    statusFilter: 'all',
    searchQuery: '',
    selectMode: false,
    batchDeleteLoading: false,
    batchPackageLoading: false,
    batchRerunLoading: false,
    batchRunToLoading: false,
    continueLoading: false,
    fetchJobs: async () => {},
    setJobsAndFinishLoading: () => {},
    resetForWorkspace: () => {},
    failJobFetch: () => {},
    setStatusFilter: () => {},
    setSearchQuery: () => {},
    toggleSelectMode: () => {},
    toggleSelect: () => {},
    selectAll: () => {},
    selectFailed: () => {},
    clearSelection: () => {},
    toggleExpand: () => {},
    getFilteredJobs: () => [],
    batchRerun: async () => ({ results: [] }) as BatchJobMutationResult,
    batchDelete: async () => ({ results: [] }) as BatchJobMutationResult,
    batchPackage: async () =>
      ({
        failed_count: 0,
        results: [],
        succeeded_count: 0,
      }) as WorkspacePackageResult,
    batchRunTo: async () => ({ results: [] }) as BatchJobMutationResult,
    continueJob: async () => ({
      job_id: '',
      operation: '',
      status: '',
    }),
    ...partial,
  }
}
