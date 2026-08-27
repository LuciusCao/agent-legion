import type { components } from '../generated/api'

export type FailedNodeRunItem = components['schemas']['FailedNodeRunItem']
export type FailedNodeRunsResponse =
  components['schemas']['FailedNodeRunsResponse']
export type JobRerunByFailureRequest =
  components['schemas']['JobRerunByFailureRequest']
export type JobRerunByFailureResponse =
  components['schemas']['JobRerunByFailureResponse']
export type FailureCategory = JobRerunByFailureRequest['category']

export type RerunByFailureInput = {
  category: FailureCategory
  jobIds?: string[]
  fromNodeKey?: string
}

export type RerunByFailureCategoryAction = (
  workspaceId: string,
  input: RerunByFailureInput
) => Promise<JobRerunByFailureResponse>
