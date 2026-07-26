import type { JobState } from '../state'
import type * as JobTypes from '../../../types/jobTypes'
import { createOptionAccumulator } from '../filterLogic/optionAccumulator'
import {
  computeFilterCounts,
  computeFilteredJobIds,
} from '../filterLogic/incrementalFilters'
import { initialJobDataState } from '../initialState'

const emptyBatch = { failed_count: 0, results: [], succeeded_count: 0 }

export function createJobSummary(
  partial: Partial<JobTypes.JobSummary> = {}
): JobTypes.JobSummary {
  // prettier-ignore
  return {
    active_node_key: null, batch_id: '', completed_nodes: 0, created_at: '', error_message: '', error_summary: '', id: '', node_summaries: undefined, source_id: '', source_type: '', status: '', storage_dir: '', title: '', total_nodes: 0, updated_at: '', workflow_key: '', workspace_id: '', workflow_revision_id: '', workflow_version: null, workflow_definition_hash: '', outcome: '', current_workflow_revision_id: '', current_workflow_revision_version: null, is_workflow_outdated: false, packed: 0,
    ...partial,
  }
}

export function createJobState(partial: Partial<JobState> = {}): JobState {
  const jobs = partial.jobs ?? []
  const jobsById =
    partial.jobsById ?? Object.fromEntries(jobs.map((j) => [j.id, j]))
  const jobIds = partial.jobIds ?? jobs.map((j) => j.id)
  const jobIndexById =
    partial.jobIndexById ??
    Object.fromEntries(jobIds.map((id, index) => [id, index]))
  const filterConfig = partial.filterConfig ?? {
    status: null,
    search: '',
    workflowVersion: null,
    activeNodeKey: null,
  }
  return {
    ...initialJobDataState,
    jobs,
    jobsById,
    jobIds,
    jobIndexById,
    revision: 0,
    filteredJobIds:
      partial.filteredJobIds ??
      computeFilteredJobIds(jobIds, jobsById, filterConfig),
    filterCounts:
      partial.filterCounts ??
      computeFilterCounts(jobIds, jobsById, filterConfig),
    optionAccumulator: createOptionAccumulator(jobs),
    filterConfig,
    batchDeleteLoading: false,
    batchPackageLoading: false,
    batchClearPackedLoading: false,
    batchRerunLoading: false,
    batchRunToLoading: false,
    continueLoading: false,
    batchUpgradeWorkflowLoading: false,
    fetchJobs: async () => {},
    setJobsAndFinishLoading: () => {},
    resetForWorkspace: () => {},
    failJobFetch: () => {},
    setJobsSnapshot: () => {},
    appendJobsSnapshot: () => {},
    applyJobPatchBatch: () => {},
    setFilterConfig: () => {},
    toggleSelectMode: () => {},
    toggleSelect: () => {},
    selectAll: () => {},
    selectFailed: () => {},
    selectUnpacked: () => {},
    clearSelection: () => {},
    toggleExpand: () => {},
    getFilteredJobs: () => [],
    batchRerun: async () => ({ results: [] }),
    rerunByFailureCategory: async () => ({ results: [] }),
    batchDelete: async () => ({ results: [] }),
    batchPackage: async () => emptyBatch,
    batchClearPacked: async () => emptyBatch,
    batchRunTo: async () => ({ results: [] }),
    batchUpgradeWorkflow: async () => ({ results: [] }),
    continueJob: async () => ({ job_id: '', operation: '', status: '' }),
    ...partial,
  }
}
