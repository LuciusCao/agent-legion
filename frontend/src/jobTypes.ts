import type { components } from './generated/api'

export type JobSummary = components['schemas']['JobSummaryResponse']
export type JobNodeSummary = components['schemas']['JobNodeSummaryResponse']
export type JobDetail = components['schemas']['JobDetailResponse']
export type JobNode = components['schemas']['JobNodeResponse']
export type NodeRun = components['schemas']['NodeRunResponse']
export type JobsResponse = components['schemas']['JobsResponse']
export type JobBatchResponse = components['schemas']['JobBatchResponse']
export type JobMutationResult =
  components['schemas']['JobMutationResultResponse']
export type BatchJobMutationResult =
  components['schemas']['BatchJobMutationResponse']
export type JobBatchRerunRequest = components['schemas']['JobBatchRerunRequest']
export type WorkspacePackageResult =
  components['schemas']['WorkspacePackageResponse']
export type WorkspacePackageResultItem =
  components['schemas']['WorkspacePackageResultResponse']
export type RunToRequest =
  components['schemas']['server__app__routes__job_operation_contracts__RunToRequest']
export type ContinueJobRequest = components['schemas']['ContinueJobRequest']
export type BatchRunToRequest =
  components['schemas']['server__app__routes__job_operation_contracts__BatchRunToRequest']
