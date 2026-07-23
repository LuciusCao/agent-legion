import type { components } from '../generated/api'

export type JobSummary = components['schemas']['JobSummaryResponse']
export type JobNodeSummary = components['schemas']['JobNodeSummaryResponse']
export type JobDetail = components['schemas']['JobDetailResponse']
export type JobNode = components['schemas']['JobNodeResponse']
export type ExecutorKind = NonNullable<JobNode['executor_kind']>
export type NodeRun = components['schemas']['NodeRunResponse']
export type JobsResponse = components['schemas']['JobsResponse']
export type JobBatchResponse = components['schemas']['JobBatchResponse']
export type JobLogResponse = components['schemas']['JobLogResponse']
export type JobMutationResult =
  components['schemas']['JobMutationResultResponse']
export type BatchJobMutationResult =
  components['schemas']['BatchJobMutationResponse']
export type JobBatchRerunRequest = components['schemas']['JobBatchRerunRequest']
export type BatchJobIdsRequest = components['schemas']['BatchJobIdsRequest']
export type WorkspacePackageResult =
  components['schemas']['WorkspacePackageResponse']
export type WorkspacePackageResultItem =
  components['schemas']['WorkspacePackageResultResponse']
export type RunToRequest = components['schemas']['RunToRequest']
export type BatchRunToRequest = components['schemas']['BatchRunToRequest']
export type ContinueJobRequest = components['schemas']['ContinueJobRequest']
