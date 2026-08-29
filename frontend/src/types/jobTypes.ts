import type { components } from '../generated/api'

export type JobSummary = components['schemas']['JobSummaryResponse']
export type JobNodeSummary = components['schemas']['JobNodeSummaryResponse']
export type JobDetail = components['schemas']['JobDetailResponse']
export type JobNode = components['schemas']['JobNodeResponse']
export type ExecutorKind = NonNullable<JobNode['executor_kind']>
export type NodeRun = components['schemas']['NodeRunResponse']
export type JobsResponse = components['schemas']['JobsResponse']
export type JobsPageResponse = components['schemas']['JobsPageResponse']
export type JobFacetsResponse = components['schemas']['JobFacetsResponse']
export type JobListFilterParams = components['schemas']['JobFilterPayload']
export type JobBatchResponse = components['schemas']['JobBatchResponse']
export type JobLogResponse = components['schemas']['JobLogResponse']
export type JobMutationResult =
  components['schemas']['JobMutationResultResponse']
export type BatchJobMutationResult =
  components['schemas']['BatchJobMutationResponse']
export type JobBatchRerunRequest = components['schemas']['JobBatchRerunRequest']
export type JobBatchRerunPreviewRequest =
  components['schemas']['JobBatchRerunPreviewRequest']
export type BatchRerunPreviewResult =
  components['schemas']['BatchRerunPreviewResponse']
export type BatchJobIdsRequest = components['schemas']['BatchJobIdsRequest']
export type WorkspacePackageResult =
  components['schemas']['WorkspacePackageResponse']
export type WorkspacePackageStatusResetResult =
  components['schemas']['WorkspacePackageStatusResetResponse']
export type RunToRequest = components['schemas']['RunToRequest']
export type BatchRunToRequest = components['schemas']['BatchRunToRequest']
export type ContinueJobRequest = components['schemas']['ContinueJobRequest']
