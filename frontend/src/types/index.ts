import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

// Job domain types live in ./jobTypes (single source of truth, e.g.
// JobSummary); this barrel only re-exports them for '../types' consumers.
export type {
  JobSummary,
  JobNodeSummary,
  JobDetail,
  JobNode,
  NodeRun,
  JobsResponse,
  JobBatchResponse,
} from './jobTypes'
export type {
  TokenUsageRunResponse,
  TokenUsageJobResponse,
  TokenUsageWorkspaceResponse,
} from './tokenUsageTypes'

export type ContentType = 'knowledge' | 'question'

export type InteractionStats = {
  passed: number
  total: number
}

/**
 * Agent status as pushed over the /api/agents WebSocket.
 *
 * Derived from the generated HTTP contract; the WS broadcast
 * (AgentStatusManager.to_dicts) additionally sends workspace_id, task_count
 * and max_tasks, which the HTTP GET projection intentionally omits.
 */
export type AgentStatus = ApiSchemas['AgentStatusResponse'] & {
  workspace_id: string
  task_count: number
  max_tasks: number
}

export type Chapter = {
  id?: string
  start: number
  end?: number
  title: string
}

export type InteractionOption = {
  id: string
  text: string
  is_distractor: boolean
}

export type InteractionNode = {
  id?: string
  type?: string
  trigger_time?: number | string
  instruction?: string
  hint?: string
  reference_sentence?: string
  options?: InteractionOption[]
  answer?: string[]
  grading_mode?: string
}

export type VideoArtifacts = {
  subtitles: Array<{ index: number; start: number; end: number; text: string }>
  chapters: Chapter[]
  interactions: InteractionNode[]
  metadata: Record<string, unknown> | null
  review: Record<string, unknown> | null
  checklist: Record<string, unknown> | null
}

export type WorkspaceRecord = ApiSchemas['WorkspaceRecord']
export type WorkspaceResponse = ApiSchemas['WorkspaceResponse']
export type WorkspaceConfigurationResponse =
  ApiSchemas['WorkspaceConfigurationResponse']
export type WorkflowDraftValidationResponse =
  ApiSchemas['WorkflowDraftValidationResponse']
export type WorkerStatusResponse = ApiSchemas['WorkerStatusResponse']

/**
 * JSON-Schema subset the backend uses to declare configurable node
 * parameters. The generated OpenAPI types expose these blobs as
 * `{[key: string]: unknown}` (e.g. node config schemas on the settings
 * payload); these types are the client-side interpretation of that contract.
 */
export type ConfigSchemaProperty = {
  type: 'string' | 'integer' | 'number' | 'boolean'
  default?: string | number | boolean
  enum?: (string | number)[]
  minimum?: number
  maximum?: number
  description?: string
  secret?: boolean
  secret_ref?: boolean
  runtime_mutable?: boolean
}

export type ConfigSchema = {
  type?: 'object'
  properties?: Record<string, ConfigSchemaProperty>
  required?: string[]
}

/**
 * Default execution config for Agent nodes (provider/model/thinking).
 * The generated WorkspaceSettingsPayload does not spell this key out yet;
 * it arrives inside the settings blob of GET /api/workspaces/{id}/settings.
 */
export type AgentDefaults = {
  provider?: string
  model?: string
  thinking?: string
}

export type WorkspaceSettings = ApiSchemas['WorkspaceSettingsPayload'] & {
  nodeConfig?: Record<string, Record<string, unknown>>
  nodeConfigSchemas?: Record<string, ConfigSchema>
  agentDefaults?: AgentDefaults
}

export type WorkspaceSettingsResponse = ApiSchemas['WorkspaceSettingsResponse']

export type AgentListItem = ApiSchemas['AgentListItem']
export type AgentListResponse = ApiSchemas['AgentListResponse']
export type AgentDetailResponse = ApiSchemas['AgentDetailResponse']
export type AgentVersion = ApiSchemas['AgentVersionResponse']
export type AgentVersionSummary = ApiSchemas['AgentVersionSummary']
export type AgentVersionsResponse = ApiSchemas['AgentVersionsResponse']
export type AgentDefinitionPayload = ApiSchemas['AgentDefinitionPayload']
export type AgentCreateRequest = ApiSchemas['AgentCreateRequest']
export type AgentRuntime = AgentDefinitionPayload['runtime']

export type SkillValidateResponse = ApiSchemas['SkillValidateResponse']
export type SkillTagsResponse = ApiSchemas['SkillTagsResponse']

export type WorkflowNodeRecord = ApiSchemas['WorkflowNodeResponse']
export type WorkflowIntakeModeRecord = ApiSchemas['WorkflowIntakeModeResponse']
export type WorkflowDefinitionRecord = ApiSchemas['WorkflowDefinitionResponse']
export type WorkflowRevisionSummary = ApiSchemas['WorkflowRevisionSummary']
export type ActiveWorkflowRevisionResponse =
  ApiSchemas['ActiveWorkflowRevisionResponse']
export type WorkflowRevisionsResponse = ApiSchemas['WorkflowRevisionsResponse']
export type WorkflowRevisionDetailResponse =
  ApiSchemas['WorkflowRevisionDetailResponse']
export type ArtifactResponse = ApiSchemas['ArtifactResponse']

export type CreateJobBatchInput = ApiSchemas['JobBatchRequest']

export type {
  SocraticOption,
  AnswerBlank,
  AnalysisStep,
  KeyInfoPosition,
  KeyInfoContent,
  KeyInfoItem,
  PossibleErrorItem,
  ComprehensionInfo,
  QuestionNormalized,
  QuestionArtifactNormalized,
} from './comprehensionTypes'

export type WorkspacesResponse = ApiSchemas['WorkspacesResponse']
