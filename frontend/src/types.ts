import type { components } from './generated/api'

type ApiSchemas = components['schemas']

export type JobSummary = ApiSchemas['JobSummaryResponse']

export type ContentType = 'knowledge' | 'question'
export type DetailTab = 'nodes' | 'subtitles' | 'logs' | 'metadata'
export type RunToMode = 'continue' | 'rerun'

export type InteractionStats = {
  passed: number
  total: number
}

export type VideoItem = {
  id: string
  title: string
  source_url: string
  content_type: ContentType
  external_id: string
  knowledge_code: string
  question_id: string
  source_uuid: string
  status: string
  current_phase: string
  error_message: string
  storage_dir: string
  duration: number
  packed: boolean
  interaction_stats?: Record<string, InteractionStats>
  interaction_review_status?: 'all_passed' | 'partial' | 'all_failed'
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

export type GlobalServiceStatus = ApiSchemas['GlobalServicesResponse']

export type ResourceProviderDefinition =
  ApiSchemas['ResourceProviderDefinition']

export type ResourceBinding = ApiSchemas['ResourceBinding']

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

export type AddResult = {
  external_id: string
  content_type: ContentType
  status: string
  message: string
  video?: VideoItem
}

export type BatchResult = {
  video_id: string
  status: string
  phase?: string
  message: string
}

export type RunToResult = {
  video_id: string
  status: string
  phase: string
  message: string
}

export type PhaseRun = {
  id: number
  video_id: string
  phase_key: string
  status: string
  started_at: string
  finished_at: string | null
  command_json: string
  exit_code: number | null
  log_path: string
  error_message: string
  agent_id?: string
  agent_session_id?: string
}

export type TranscriptionRun = {
  id: number
  video_id: string
  provider: string
  status: string
  started_at: string
  finished_at: string | null
  srt_entry_count: number
  validation_summary: string
  fallback_reason: string
}

import type {
  JobNodeSummary,
  JobDetail,
  JobNode,
  NodeRun,
  JobsResponse,
  JobBatchResponse,
} from './jobTypes'
import type {
  TokenUsageRunResponse,
  TokenUsageJobResponse,
  TokenUsageWorkspaceResponse,
} from './tokenUsageTypes'

export type {
  JobNodeSummary,
  JobDetail,
  JobNode,
  NodeRun,
  JobsResponse,
  JobBatchResponse,
}
export type {
  TokenUsageRunResponse,
  TokenUsageJobResponse,
  TokenUsageWorkspaceResponse,
}

/** @deprecated use {@link JobSummary} directly */
export type JobRecord = JobSummary
/** @deprecated use {@link JobNode} directly */
export type JobNodeRecord = JobNode
/** @deprecated use {@link NodeRun} directly */
export type NodeRunRecord = NodeRun
/** @deprecated use {@link JobDetail} directly */
export type JobDetailResponse = JobDetail

export type WorkspaceRecord = ApiSchemas['WorkspaceRecord']
export type WorkspaceResponse = ApiSchemas['WorkspaceResponse']
export type WorkspaceConfigurationResponse =
  ApiSchemas['WorkspaceConfigurationResponse']
export type WorkspaceSettingsTestResponse =
  ApiSchemas['WorkspaceSettingsTestResponse']
export type WorkflowDraftValidationResponse =
  ApiSchemas['WorkflowDraftValidationResponse']
export type ResourceProvidersResponse = ApiSchemas['ResourceProvidersResponse']
export type WorkerStatusResponse = ApiSchemas['WorkerStatusResponse']

export type WorkspaceSettings = ApiSchemas['WorkspaceSettingsPayload']

export type WorkflowNodeRecord = ApiSchemas['WorkflowNodeResponse']
export type WorkflowIntakeModeRecord = ApiSchemas['WorkflowIntakeModeResponse']
export type WorkflowDefinitionRecord = ApiSchemas['WorkflowDefinitionResponse']
export type WorkflowResponse = ApiSchemas['WorkflowResponse']
export type WorkflowsListResponse = ApiSchemas['WorkflowsListResponse']
export type WorkflowRevisionSummary = ApiSchemas['WorkflowRevisionSummary']
export type ActiveWorkflowRevisionResponse =
  ApiSchemas['ActiveWorkflowRevisionResponse']
export type WorkflowRevisionsResponse = ApiSchemas['WorkflowRevisionsResponse']
export type WorkflowRevisionDetailResponse =
  ApiSchemas['WorkflowRevisionDetailResponse']
export type ArtifactResponse = ApiSchemas['ArtifactResponse']

export type CreateJobBatchInput = {
  workspaceId: string
  workflowKey: string
  entity?: string
  sourceKind: string
  inputField: string
  values: string[]
}

export type QuestionOption = Record<string, unknown>

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
